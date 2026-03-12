import adsk.core, adsk.fusion, traceback
import csv, os

# -----------------------------
# Column / parameter names (must match NewScript)
# -----------------------------
PRIMARY_COL   = "Primary Legend"
SECONDARY_COL = "Secondary Legend"
SUFFIX_COL    = "Suffix"

LEGEND1_PARAM      = "Legend1"
LEGEND2_PARAM      = "Legend2"
LEGEND3_PARAM      = "Legend3"
LEGEND_DEPTH_PARAM = "LegendDepth"
HEIGHT_FRONT_PARAM = "HeightFront"
HEIGHT_BACK_PARAM  = "HeightBack"

MULTI_SEP = "|"
RESERVED_COLS = {PRIMARY_COL, SECONDARY_COL, SUFFIX_COL, "Key Count", "", None}

CMD_ID = "LoadParameters_Cmd"

# -----------------------------
# Shared state between handlers
# -----------------------------
_handlers = []
_state    = {}

# -----------------------------
# CSV helpers
# -----------------------------
def _normalize_headers(h):
    return [x.strip() if x else x for x in h]

def _read_csv_dict_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return None, []
        reader.fieldnames = _normalize_headers(reader.fieldnames)
        return reader.fieldnames, list(reader)

def _parse_multi_value_cell(raw):
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    if MULTI_SEP in s:
        return [x.strip() for x in s.split(MULTI_SEP) if x.strip()]
    return [s]

# -----------------------------
# Parameter set helpers
# -----------------------------
CHAR_SUBS = {
    "\\": "＼", "|": "│", "\"": "″", "'": "'",
    "<": "‹", ">": "›", "{": "(", "}": ")",
}

def _sanitize_fusion_text(s):
    if not s:
        return ""
    for bad, good in CHAR_SUBS.items():
        s = s.replace(bad, good)
    return s

def _set_user_param_expression(design, name, raw):
    p = design.userParameters.itemByName(name)
    if not p:
        return
    v = str(raw).strip() if raw is not None else ""
    if not v:
        return
    try:
        p.expression = v
        return
    except:
        pass
    vv = _sanitize_fusion_text(v).replace("'", "''")
    p.expression = f"'{vv}'"

def _set_text_param(design, name, value):
    p = design.userParameters.itemByName(name)
    if not p:
        return
    v = _sanitize_fusion_text(value or "").replace("'", "''")
    p.expression = f"'{v}'"

def _apply_legends_and_depth(design, primary, secondary, desired_depth):
    p_txt = (primary or "").strip()
    s_txt = (secondary or "").strip()

    _set_text_param(design, LEGEND1_PARAM, "")
    _set_text_param(design, LEGEND2_PARAM, "")
    _set_text_param(design, LEGEND3_PARAM, "")

    if desired_depth is not None and str(desired_depth).strip():
        _set_user_param_expression(design, LEGEND_DEPTH_PARAM, desired_depth)

    if s_txt:
        _set_text_param(design, LEGEND2_PARAM, p_txt)
        _set_text_param(design, LEGEND3_PARAM, s_txt)
    elif p_txt:
        _set_text_param(design, LEGEND1_PARAM, p_txt)

def _selected_index(dd_input):
    for j in range(dd_input.listItems.count):
        if dd_input.listItems.item(j).isSelected:
            return j
    return 0

# -----------------------------
# Apply all chosen parameters
# -----------------------------
def _apply_state(inputs):
    design = _state['design']

    design.isComputeDeferred = True
    try:
        # Constants from Options CSV
        for name, val in _state.get('constants', {}).items():
            _set_user_param_expression(design, name, val)

        # Sweep selections
        for param_name in _state.get('sweep_param_names', []):
            dd = inputs.itemById(f'sweep_{param_name}')
            if dd:
                idx = _selected_index(dd)
                vals = _state['sweep_values'].get(param_name, [])
                if idx < len(vals):
                    _set_user_param_expression(design, param_name, vals[idx])

        # Batch row
        batch_rows = _state.get('batch_rows', [])
        if batch_rows:
            dd_row = inputs.itemById('batch_row')
            row_idx = _selected_index(dd_row) if dd_row else 0
            row = batch_rows[row_idx]

            primary   = (row.get(PRIMARY_COL)   or "").strip()
            secondary = (row.get(SECONDARY_COL) or "").strip()
            suffix    = (row.get(SUFFIX_COL)    or "").strip()

            # Batch parameter columns
            for col in _state.get('batch_param_cols', []):
                raw = row.get(col, None)
                if raw is not None and str(raw).strip():
                    _set_user_param_expression(design, col, raw)

            # Heights: batch row → row heights map fallback
            row_heights_map = _state.get('row_heights_map', {})
            batch_hf = (row.get(HEIGHT_FRONT_PARAM) or "").strip()
            batch_hb = (row.get(HEIGHT_BACK_PARAM)  or "").strip()

            if batch_hf:
                _set_user_param_expression(design, HEIGHT_FRONT_PARAM, batch_hf)
            elif suffix in row_heights_map and design.userParameters.itemByName(HEIGHT_FRONT_PARAM):
                _set_user_param_expression(design, HEIGHT_FRONT_PARAM, row_heights_map[suffix][0])

            if batch_hb:
                _set_user_param_expression(design, HEIGHT_BACK_PARAM, batch_hb)
            elif suffix in row_heights_map and design.userParameters.itemByName(HEIGHT_BACK_PARAM):
                _set_user_param_expression(design, HEIGHT_BACK_PARAM, row_heights_map[suffix][1])

            # LegendDepth precedence: batch row → options constant
            batch_ld = None
            if LEGEND_DEPTH_PARAM in _state.get('batch_fieldnames', []):
                raw_ld = row.get(LEGEND_DEPTH_PARAM, None)
                if raw_ld is not None and str(raw_ld).strip():
                    batch_ld = str(raw_ld).strip()
            desired_depth = batch_ld if batch_ld is not None else _state.get('options_legend_depth')

            _apply_legends_and_depth(design, primary, secondary, desired_depth)

    finally:
        design.isComputeDeferred = False

    design.computeAll()

# -----------------------------
# Command handlers
# -----------------------------
class ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            _apply_state(args.command.commandInputs)
        except:
            adsk.core.Application.get().userInterface.messageBox(
                "An error occurred while applying parameters:\n" + traceback.format_exc()
            )

class DestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        adsk.terminate()

class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            cmd    = args.command
            inputs = cmd.commandInputs

            batch_rows       = _state.get('batch_rows', [])
            sweep_param_names = _state.get('sweep_param_names', [])
            sweep_values     = _state.get('sweep_values', {})

            # Batch row dropdown
            if batch_rows:
                dd = inputs.addDropDownCommandInput(
                    'batch_row', 'Batch Row',
                    adsk.core.DropDownStyles.TextListDropDownStyle
                )
                for i, row in enumerate(batch_rows):
                    p   = (row.get(PRIMARY_COL)   or "").strip()
                    s   = (row.get(SECONDARY_COL) or "").strip()
                    suf = (row.get(SUFFIX_COL)    or "").strip()
                    parts = [x for x in [p, s] if x]
                    label = " | ".join(parts) if parts else f"Row {i + 1}"
                    if suf:
                        label += f"  [{suf}]"
                    dd.listItems.add(label, i == 0)

            # One dropdown per sweep parameter
            for param_name in sweep_param_names:
                vals = sweep_values.get(param_name, [])
                dd = inputs.addDropDownCommandInput(
                    f'sweep_{param_name}', param_name,
                    adsk.core.DropDownStyles.TextListDropDownStyle
                )
                for j, v in enumerate(vals):
                    dd.listItems.add(v, j == 0)

            on_execute = ExecuteHandler()
            cmd.execute.add(on_execute)
            _handlers.append(on_execute)

            on_destroy = DestroyHandler()
            cmd.destroy.add(on_destroy)
            _handlers.append(on_destroy)

        except:
            adsk.core.Application.get().userInterface.messageBox(
                "An error occurred while setting up the dialog:\n" + traceback.format_exc()
            )

# -----------------------------
# Entry point
# -----------------------------
def run(context):
    app = adsk.core.Application.get()
    ui  = app.userInterface

    global _state, _handlers
    _state    = {}
    _handlers = []

    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            ui.messageBox("No active Fusion design was found.")
            return
        _state['design'] = design

        has_anything = False

        # ---------- Options CSV
        constants         = {}
        sweep_param_names = []
        sweep_values      = {}
        options_legend_depth = None

        if ui.messageBox(
            "Would you like to load a Parameters file?\n\n"
            "Row 1: fixed values and sweep ranges (use | to separate sweep values)",
            "Load Parameters",
            adsk.core.MessageBoxButtonTypes.YesNoButtonType
        ) == adsk.core.DialogResults.DialogYes:
            dlg = ui.createFileDialog()
            dlg.title  = "Select Parameters File"
            dlg.filter = "CSV files (*.csv)"
            if dlg.showOpen() == adsk.core.DialogResults.DialogOK:
                opt_fieldnames, opt_rows = _read_csv_dict_rows(dlg.filename)
                if opt_rows:
                    has_anything = True
                    row0 = opt_rows[0]
                    for h in (opt_fieldnames or []):
                        if not h:
                            continue
                        name = h.strip()
                        if not name or name in RESERVED_COLS:
                            continue
                        if not design.userParameters.itemByName(name):
                            continue
                        vals = _parse_multi_value_cell(row0.get(name))
                        if not vals:
                            continue
                        if len(vals) == 1:
                            constants[name] = vals[0]
                        else:
                            sweep_param_names.append(name)
                            sweep_values[name] = vals
                    options_legend_depth = constants.get(LEGEND_DEPTH_PARAM)

        _state['constants']          = constants
        _state['sweep_param_names']  = sweep_param_names
        _state['sweep_values']       = sweep_values
        _state['options_legend_depth'] = options_legend_depth

        # ---------- Row Heights CSV
        row_heights_map = {}
        if ui.messageBox(
            "Would you like to load a Heights file?\n\n"
            "Provides fallback HeightFront and HeightBack values by Suffix.",
            "Load Parameters",
            adsk.core.MessageBoxButtonTypes.YesNoButtonType
        ) == adsk.core.DialogResults.DialogYes:
            dlg = ui.createFileDialog()
            dlg.title  = "Select Heights File"
            dlg.filter = "CSV files (*.csv)"
            if dlg.showOpen() == adsk.core.DialogResults.DialogOK:
                rh_fieldnames, rh_rows = _read_csv_dict_rows(dlg.filename)
                if rh_rows and "Suffix" in (rh_fieldnames or []):
                    has_anything = True
                    for r in rh_rows:
                        suf = (r.get("Suffix") or "").strip()
                        hf  = (r.get(HEIGHT_FRONT_PARAM) or "").strip()
                        hb  = (r.get(HEIGHT_BACK_PARAM)  or "").strip()
                        if suf and hf and hb:
                            row_heights_map[suf] = (hf, hb)
        _state['row_heights_map'] = row_heights_map

        # ---------- Batch CSV
        batch_rows      = []
        batch_fieldnames = []
        batch_param_cols = []

        if ui.messageBox(
            "Would you like to load a Batch file?\n\n"
            "Allows selecting a specific row to apply.",
            "Load Parameters",
            adsk.core.MessageBoxButtonTypes.YesNoButtonType
        ) == adsk.core.DialogResults.DialogYes:
            dlg = ui.createFileDialog()
            dlg.title  = "Select Batch File"
            dlg.filter = "CSV files (*.csv)"
            if dlg.showOpen() == adsk.core.DialogResults.DialogOK:
                batch_fieldnames, batch_rows = _read_csv_dict_rows(dlg.filename)
                if batch_rows:
                    has_anything = True
                    for col in (batch_fieldnames or []):
                        if not col:
                            continue
                        c = col.strip()
                        if not c or c in RESERVED_COLS:
                            continue
                        if design.userParameters.itemByName(c):
                            batch_param_cols.append(c)

        _state['batch_rows']       = batch_rows
        _state['batch_fieldnames'] = batch_fieldnames or []
        _state['batch_param_cols'] = batch_param_cols

        if not has_anything:
            ui.messageBox("No files were loaded. There is nothing to apply.")
            return

        # If there are no choices to make, apply directly without a dialog
        needs_dialog = bool(batch_rows) or bool(sweep_param_names)

        if not needs_dialog:
            design.isComputeDeferred = True
            try:
                for name, val in constants.items():
                    _set_user_param_expression(design, name, val)
            finally:
                design.isComputeDeferred = False
            design.computeAll()
            ui.messageBox(f"Successfully applied {len(constants)} parameter(s).")
            return

        # Show selection dialog
        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()
        cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, 'Load Parameters', '')

        on_created = CommandCreatedHandler()
        cmd_def.commandCreated.add(on_created)
        _handlers.append(on_created)

        cmd_def.execute()
        adsk.autoTerminate(False)

    except:
        ui.messageBox("An unexpected error occurred:\n" + traceback.format_exc())
