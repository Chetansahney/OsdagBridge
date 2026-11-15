"""
Apply a value edited directly on the 2D CAD to the bridge input dictionary.

Only fundamental parameters can be edited on the CAD. Derived values (overhang,
overall bridge width, cross bracing spacing) are recomputed here from the edited
value, the same way the Basic Inputs dock and the Additional Inputs dialog do it,
so the redrawn geometry always satisfies

    overall width = (no. of girders - 1) * girder spacing + 2 * overhang
"""

from copy import deepcopy

from osdagbridge.core.utils.common import (
    KEY_SPAN, KEY_CARRIAGEWAY_WIDTH, KEY_TS_GIRDER_SPACING, KEY_TS_DECK_THICKNESS,
    KEY_TS_NO_OF_GIRDERS, KEY_TS_DECK_OVERHANG, KEY_TS_OVERALL_WIDTH,
    KEY_TS_FOOTPATH_WIDTH, KEY_TS_NO_OF_FOOTPATHS, KEY_CB_WIDTH, KEY_RL_WIDTH, KEY_MD_WIDTH,
    KEY_MP_CB_SPACING, KEY_MP_CB_NO_OF_CROSS_BRACINGS,
)
from .defaults import solve_extend_basic_input_dict
from .initial_sizing import BridgeConfigurationSolver
from .validator import BridgeInputValidator

# Keys entered on the Basic Inputs dock vs. the Additional Inputs dialog
CAD_EDITABLE_BASIC_KEYS = (KEY_SPAN, KEY_CARRIAGEWAY_WIDTH)
CAD_EDITABLE_ADDITIONAL_KEYS = (KEY_TS_GIRDER_SPACING, KEY_TS_DECK_THICKNESS)


class CadEditError(ValueError):
    """The edited value was rejected; the message is meant for the user."""


def apply_cad_edit(input_dict: dict, key: str, value: float, validator=None) -> None:
    """Validate ``value`` for ``key`` and commit it with its derived values.

    Works on a copy and only writes ``input_dict`` (in place, keeping the same
    object) once everything is consistent. Raises CadEditError otherwise.
    """
    if key not in CAD_EDITABLE_BASIC_KEYS + CAD_EDITABLE_ADDITIONAL_KEYS:
        raise CadEditError("This value cannot be edited from the CAD.")

    trial = deepcopy(input_dict)
    trial[key] = float(value)

    validator = validator or BridgeInputValidator()
    if key in CAD_EDITABLE_BASIC_KEYS:
        problem = validator.validate_basic_inputs(key, trial)
    else:
        problem = validator.validate_additional_inputs(key, trial)
    if problem is not None:
        raise CadEditError(problem[1])

    try:
        if key == KEY_CARRIAGEWAY_WIDTH:
            # Overall width changes: re-solve spacing and overhang for the same girder count
            solve_extend_basic_input_dict(trial)
        elif key == KEY_TS_GIRDER_SPACING:
            _solve_for_girder_spacing(trial)
        elif key == KEY_SPAN:
            _update_cross_bracing_spacing(trial)
    except (ValueError, TypeError) as exc:
        raise CadEditError(str(exc)) from exc

    input_dict.clear()
    input_dict.update(trial)


def _layout_solver(d: dict) -> BridgeConfigurationSolver:
    """Solver for the current deck make-up (same inputs as the Additional Inputs layout)."""
    railing = float(d.get(KEY_RL_WIDTH) or 0.0)
    return BridgeConfigurationSolver(
        carriageway_width=float(d[KEY_CARRIAGEWAY_WIDTH]),
        crash_barrier_width=float(d[KEY_CB_WIDTH]),
        footpath_width=float(d.get(KEY_TS_FOOTPATH_WIDTH) or 0.0),
        railing_width=railing / 1000.0 if railing > 10 else railing,   # mm or m, as elsewhere
        median_width=float(d.get(KEY_MD_WIDTH) or 0.0),
        n_footpaths=int(d.get(KEY_TS_NO_OF_FOOTPATHS) or 0),
    )


def _solve_for_girder_spacing(d: dict) -> None:
    """Girder count stays fixed; overhang follows from the new spacing."""
    result = _layout_solver(d)._solve_layout(
        no_of_girders=int(float(d[KEY_TS_NO_OF_GIRDERS])),
        girder_spacing=float(d[KEY_TS_GIRDER_SPACING]),
        changed_field="spacing",
    )
    d.update({
        KEY_TS_GIRDER_SPACING: result.girder_spacing,
        KEY_TS_DECK_OVERHANG:  result.deck_overhang,
        KEY_TS_OVERALL_WIDTH:  result.overall_width,
    })


def _update_cross_bracing_spacing(d: dict) -> None:
    """Bracing spacing = span / (no. of cross bracings + 1), as in Additional Inputs."""
    count = d.get(KEY_MP_CB_NO_OF_CROSS_BRACINGS)
    if count in (None, ""):
        return
    d[KEY_MP_CB_SPACING] = round(float(d[KEY_SPAN]) / (int(float(count)) + 1), 3)
