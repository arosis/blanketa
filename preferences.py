import crash_report
import support_reminder
import time_stamps
import update_check

TIME_STAMP_TEXTS = ("url", "user")
SWITCHES = ("update_checks", "crash_offers", "support_reminder")


def current():
    stamp = time_stamps.settings()
    return {
        "update_checks": update_check.enabled(),
        "crash_offers": crash_report.enabled(),
        "support_reminder": not support_reminder.hidden(),
        "time_stamp": {
            "service": stamp["service"],
            "url": stamp["url"],
            "user": stamp["user"],
            "usable": time_stamps.usable(stamp),
        },
    }


def _checked_time_stamp(wanted):
    services = (time_stamps.PUBLIC_SERVICE, time_stamps.OWN_SERVICE)
    well_formed = (
        isinstance(wanted, dict)
        and set(wanted) <= {"service", *TIME_STAMP_TEXTS}
        and all(isinstance(value, str) for value in wanted.values())
        and wanted.get("service", time_stamps.PUBLIC_SERVICE) in services
    )
    if not well_formed:
        raise ValueError("Nastavení časových razítek nemá očekávaný tvar.")


def _check(wanted):
    if not isinstance(wanted, dict) or not set(wanted) <= {*SWITCHES, "time_stamp"}:
        raise ValueError("Nastavení nemá očekávaný tvar.")
    if not all(isinstance(wanted[name], bool) for name in SWITCHES if name in wanted):
        raise ValueError("Nastavení nemá očekávaný tvar.")
    if "time_stamp" in wanted:
        _checked_time_stamp(wanted["time_stamp"])


def _store_time_stamp(wanted):
    stored = {**time_stamps.settings(), **wanted}
    time_stamps.store_service(stored["service"], stored["url"].strip(), stored["user"].strip())


def change(wanted):
    _check(wanted)
    if "time_stamp" in wanted:
        _store_time_stamp(wanted["time_stamp"])
    if "update_checks" in wanted:
        update_check.set_enabled(wanted["update_checks"])
    if "crash_offers" in wanted:
        crash_report.set_enabled(wanted["crash_offers"])
    if "support_reminder" in wanted:
        if wanted["support_reminder"]:
            support_reminder.show_again()
        else:
            support_reminder.hide()
    return current()
