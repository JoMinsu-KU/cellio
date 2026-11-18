"""Canonical rule documents. Scene coordinates are expressed in metres."""
from dataclasses import dataclass
from cellio.core.models import CellIOError


@dataclass(frozen=True)
class Profile:
    prefix: str
    output_center: tuple
    points: dict
    stop_endpoints: tuple
    belt: bool
    fixture: bool


PROFILES = {"cell_a": Profile("A",(.45,.25),{
    "park":(.30,0.,1.10), "pickup_hover":(.45,-.116,1.030),"pickup_grasp":(.45,-.116,.815),
    "transfer_mid":(.45,.08,1.030),"output_hover":(.45,.25,1.030),"output_place":(.45,.25,.815)},
    ("control.operation","actuator.robot","actuator.gripper","actuator.belt"),True,False)}


def profile_for(cell):
    try:
        return PROFILES[cell]
    except KeyError as exc:
        raise CellIOError("CFG_SCENE",str(cell)) from exc


def preset(cell_id="cell_a", horizon_steps=7200):
    p = profile_for(cell_id)
    names = {"01":"state_valid","02":"input_fresh","03":"command_order","04":"response_or_abort",
             "05":"abort_applied","06":"abort_stopped","07":"pick_precondition","08":"grasp_before_lift",
             "09":"carry","10":"belt_before_approach" if p.belt else "clamp_clear","11":"process",
             "12":"unload","13":"declared_done","14":"goal","15":"restore","15B":"no_tail",
             "16":"reset_isolation","17":"registered_actions"}
    def rule(num,kind,trigger,name,*,lower=0,upper=0,dwell=0,category="compliance",scope=None,applies=None):
        item = {"id":f"{cell_id}.r{num.lower()}.{names[num]}","category":category,"kind":kind,"scope":scope or ("episode" if trigger is None else "operation"),
                "trigger":trigger,"predicate":{"op":"pred","name":name},"required_coverage":False}
        if applies:
            item["applicability"] = {"op":"pred","name":applies}
        if kind in ("response","bounded_response_or_abort","dwell"):
            item.update(window={"lower_steps":lower,"upper_steps":upper},dwell_steps=dwell)
        if kind=="bounded_response_or_abort":
            item.update(abort_matcher="command.closed",abort_grace_steps=3)
        return item
    rules = [
        rule("01","invariant",None,"state_valid"),
        rule("02","admission","command.freshness","input_fresh"),
        rule("03","order","command.audit","command_order"),
        rule("04","bounded_response_or_abort","command.response","actual_response",lower=1,upper=960),
        rule("05","response","operation.abort","all_targets_held",upper=8),
        rule("06","response","operation.abort","physical_quiescent",upper=72,dwell=12),
        rule("07","admission","action.pick","pick_ready"),
        rule("08","admission","action.lift","actual_grasp_contact"),
        rule("09","invariant",None,"carry_valid",applies="carry_active"),
    ]
    if p.belt:
        rules.append(rule("10","admission","action.approach","belt_dwell"))
    if p.fixture:
        rules.extend((rule("10","admission","action.clamp_close","clamp_ready"),
                      rule("11","invariant",None,"process_valid",applies="process_active"),
                      rule("12","admission","action.unload","clamp_open_dwell")))
    rules.extend((
        rule("13","admission","operation.done","declared_goal"),
        rule("14","response",None,"goal_sample",upper=horizon_steps,dwell=60,category="task"),
        rule("15","response","path.ended","path_restored",upper=12,category="coverage"),
        rule("15B","invariant","path.ended","no_stale_tail",scope="after_event",category="coverage"),
        rule("16","invariant","reset.compare","reset_clean",scope="after_event",category="coverage"),
        rule("17","invariant",None,"registered_actions",category="coverage"),
    ))
    return tuple(rules)
