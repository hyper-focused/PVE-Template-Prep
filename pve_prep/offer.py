# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Enrollment offers for create and disk-image upgrade runs.

Before any download, selected VMIDs that already have a record are asked
keep / change schedule / turn off. That choice is stored for the run and
applied only to builds that succeed. After the builds, templates that had
no prior record get the weekly / monthly / none prompt. Standalone image
jobs skip both asks. Each enrollment keeps its own schedule. weekly and
monthly cron wrappers can both exist; each refreshes only records on that
cadence. none keeps the JSON for interactive Update without a cron refresh.
When the last weekly or monthly record is gone, that cron wrapper is removed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Literal

from pve_prep.cronscript import (
    CronScriptError,
    cron_script,
    install_cron_script,
    remove_cron_script,
)
from pve_prep.enroll import (
    CRON_SCHEDULES,
    Enrollment,
    EnrollmentError,
    enrollments_for,
    load_enrollment,
    load_enrollments,
    save_enrollment,
)
from pve_prep.prompts import (
    PromptAbort,
    ask_prior_enrollment,
    ask_turn_off_updates,
    ask_unattended_schedule,
)

PriorAction = Literal["keep", "none", "weekly", "monthly"]


@dataclass(frozen=True)
class PriorEnrollmentPlan:
    """Choice recorded before downloads for already-enrolled selected VMIDs."""

    vmids: frozenset[int]
    action: PriorAction
    records: tuple[Enrollment, ...]


def _record_json(record: Enrollment) -> str:
    """One JSON object, same fields as the enrollment file."""
    payload = {
        "vmid": record.vmid,
        "distro": record.distro,
        "release": record.release,
        "digest": record.digest,
        "checksum_alg": record.checksum_alg,
        "disk_digest": record.disk_digest,
        "storage": record.storage,
        "password_auth": record.password_auth,
        "permit_root": record.permit_root,
        "schedule": record.schedule,
    }
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def load_prior_enrollments(
    vmids: tuple[int, ...] | list[int],
    *,
    directory,
) -> list[Enrollment]:
    """Enrollment records for selected VMIDs that already have a file."""
    records: list[Enrollment] = []
    for vmid in vmids:
        try:
            record = load_enrollment(vmid, directory=directory)
        except EnrollmentError:
            continue
        if record is not None:
            records.append(record)
    records.sort(key=lambda item: item.vmid)
    return records


def plan_prior_enrollments(
    read_line,
    write,
    vmids: tuple[int, ...] | list[int],
    *,
    directory,
) -> PriorEnrollmentPlan | None:
    """Ask keep/change/off when any selected VMID is already enrolled.

    None means none of the VMIDs had a record. Standalone callers must not
    call this. PromptAbort propagates unchanged.
    """
    records = load_prior_enrollments(vmids, directory=directory)
    if not records:
        return None
    action = ask_prior_enrollment(read_line, write, records)
    return PriorEnrollmentPlan(
        vmids=frozenset(record.vmid for record in records),
        action=action,  # type: ignore[arg-type]
        records=tuple(records),
    )


def reconcile_cron_scripts(
    write,
    *,
    program: str,
    directory,
    cron_directory,
    dry_run: bool = False,
) -> None:
    """Drop our cron wrapper for every cadence that has no enrollments left."""
    for schedule in CRON_SCHEDULES:
        try:
            remaining = enrollments_for(schedule, directory=directory)
        except EnrollmentError as exc:
            write(f"Could not check automated update records for {schedule}: {exc}\n")
            continue
        if remaining:
            continue
        if dry_run:
            write(
                f"Dry run: /etc/cron.{schedule}/pve-template-prep would be removed "
                "if it is still ours and no templates remain on that schedule.\n"
            )
            continue
        try:
            removed = remove_cron_script(program, schedule, directory=cron_directory)
        except CronScriptError as exc:
            write(f"{exc}\n")
            continue
        if removed:
            write(
                f"Removed the {schedule} automated-update cron script; "
                "no templates remain on that schedule.\n"
            )


def _install_schedule(
    write,
    *,
    program: str,
    schedule: str,
    cron_directory,
    dry_run: bool,
) -> None:
    if schedule not in CRON_SCHEDULES:
        return
    if dry_run:
        try:
            write(cron_script(program, schedule))
        except CronScriptError as exc:
            write(f"{exc}\n")
        return
    try:
        install_cron_script(program, schedule, directory=cron_directory)
    except CronScriptError as exc:
        write(f"{exc}\n")


def _set_all_schedules_none(write, *, directory, dry_run: bool) -> None:
    """Keep every enrollment file; set schedule to none."""
    try:
        records = load_enrollments(directory=directory)
    except EnrollmentError as exc:
        write(f"Automated update records could not be read: {exc}\n")
        return
    for record in records:
        if record.schedule == "none":
            continue
        if dry_run:
            write(
                f"Dry run: VMID {record.vmid} would keep its enrollment with "
                "schedule none (no automated updates).\n"
            )
            continue
        try:
            save_enrollment(replace(record, schedule="none"), directory=directory)
        except EnrollmentError as exc:
            write(
                f"Automated updates for VMID {record.vmid} could not be turned off: "
                f"{exc}\n"
            )
            continue
        write(
            f"Automated updates for VMID {record.vmid} were turned off; "
            "the enrollment record was kept for interactive updates.\n"
        )


def _schedule_for_prior(plan: PriorEnrollmentPlan, vmid: int) -> str:
    """Schedule to write for one prior VMID on keep or change."""
    if plan.action in {"weekly", "monthly", "none"}:
        return plan.action
    for record in plan.records:
        if record.vmid == vmid:
            return record.schedule
    raise LookupError(f"VMID {vmid} is not in the prior enrollment plan")


def _rewrite_enrollment(
    write,
    *,
    release: str,
    vmid: int,
    distro: str,
    password_auth: bool,
    permit_root: bool,
    schedule: str,
    digest_for,
    pin_for,
    directory,
    dry_run: bool,
) -> bool:
    """Rewrite one enrollment after a successful build. False when skipped."""
    try:
        digest, checksum_alg = digest_for(distro, release)
        storage, disk_digest = pin_for(vmid)
        record = Enrollment(
            vmid=vmid,
            distro=distro,
            release=release,
            digest=digest,
            checksum_alg=checksum_alg,
            disk_digest=disk_digest,
            storage=storage,
            password_auth=password_auth,
            permit_root=permit_root,
            schedule=schedule,
        )
        if not dry_run:
            save_enrollment(record, directory=directory)
    except PromptAbort:
        raise
    except Exception as exc:
        write(f"Enrollment for {release} was not saved: {exc}\n")
        return False
    if dry_run:
        write(_record_json(record))
    elif schedule == "none":
        write(
            f"Enrollment for VMID {vmid} saved with no automated updates "
            "(interactive Update can still find it).\n"
        )
    return True


def apply_prior_enrollment(
    write,
    *,
    plan: PriorEnrollmentPlan,
    created: list[tuple[str, int]],
    distro: str,
    password_auth: bool,
    permit_root: bool,
    digest_for,
    pin_for,
    program: str,
    directory,
    cron_directory,
    dry_run: bool = False,
) -> None:
    """Apply a pre-download plan to successful builds for those VMIDs.

    keep or change rewrites digest, storage, disk_digest, and schedule.
    Turning updates off sets schedule to none and keeps the JSON. Only this
    run's VMIDs change schedule; other enrollments are left alone. Cron
    wrappers with nothing left on weekly/monthly are removed. created must
    already be filtered to successful prior VMIDs.
    """
    if not created:
        return
    saved_schedules: set[str] = set()
    for release, vmid in created:
        try:
            schedule = _schedule_for_prior(plan, vmid)
        except LookupError as exc:
            write(f"{exc}\n")
            continue
        if _rewrite_enrollment(
            write,
            release=release,
            vmid=vmid,
            distro=distro,
            password_auth=password_auth,
            permit_root=permit_root,
            schedule=schedule,
            digest_for=digest_for,
            pin_for=pin_for,
            directory=directory,
            dry_run=dry_run,
        ):
            saved_schedules.add(schedule)
    for schedule in sorted(saved_schedules):
        _install_schedule(
            write,
            program=program,
            schedule=schedule,
            cron_directory=cron_directory,
            dry_run=dry_run,
        )
    reconcile_cron_scripts(
        write,
        program=program,
        directory=directory,
        cron_directory=cron_directory,
        dry_run=dry_run,
    )


def _offer_turn_off_existing(
    read_line,
    write,
    *,
    program: str,
    directory,
    cron_directory,
    dry_run: bool,
) -> None:
    try:
        existing = load_enrollments(directory=directory)
    except EnrollmentError as exc:
        write(f"Automated update records could not be read: {exc}\n")
        return
    if not existing:
        return
    if not ask_turn_off_updates(read_line, write, count=len(existing)):
        return
    _set_all_schedules_none(write, directory=directory, dry_run=dry_run)
    reconcile_cron_scripts(
        write,
        program=program,
        directory=directory,
        cron_directory=cron_directory,
        dry_run=dry_run,
    )


def _apply_new_schedule(
    write,
    *,
    created: list[tuple[str, int]],
    distro: str,
    password_auth: bool,
    permit_root: bool,
    digest_for,
    pin_for,
    program: str,
    directory,
    cron_directory,
    schedule: str,
    dry_run: bool,
) -> str:
    """Write enrollments for this run only. Does not rewrite other VMIDs."""
    for release, vmid in created:
        _rewrite_enrollment(
            write,
            release=release,
            vmid=vmid,
            distro=distro,
            password_auth=password_auth,
            permit_root=permit_root,
            schedule=schedule,
            digest_for=digest_for,
            pin_for=pin_for,
            directory=directory,
            dry_run=dry_run,
        )
    _install_schedule(
        write,
        program=program,
        schedule=schedule,
        cron_directory=cron_directory,
        dry_run=dry_run,
    )
    reconcile_cron_scripts(
        write,
        program=program,
        directory=directory,
        cron_directory=cron_directory,
        dry_run=dry_run,
    )
    return schedule


def offer_created(
    read_line,
    write,
    *,
    created: list[tuple[str, int]],
    distro: str,
    password_auth: bool,
    permit_root: bool,
    digest_for,
    pin_for,
    program: str,
    directory,
    cron_directory,
    dry_run: bool = False,
) -> str | None:
    """Ask, then enroll templates that had no prior record.

    created is (release, vmid) pairs with no enrollment before this run.
    pin_for(vmid) returns (storage, disk_digest) for the boot disk this run
    left in place. When created is empty but enrollments already exist, the
    operator can turn automated updates off (schedule none) on every record.
    Returns the schedule written for this run (weekly, monthly, or none), or
    None when nothing was offered. Each template keeps its own schedule;
    earlier enrollments are not moved. PromptAbort propagates and changes no
    record.
    """
    if not created:
        _offer_turn_off_existing(
            read_line,
            write,
            program=program,
            directory=directory,
            cron_directory=cron_directory,
            dry_run=dry_run,
        )
        return None
    schedule = ask_unattended_schedule(read_line, write)
    return _apply_new_schedule(
        write,
        created=created,
        distro=distro,
        password_auth=password_auth,
        permit_root=permit_root,
        digest_for=digest_for,
        pin_for=pin_for,
        program=program,
        directory=directory,
        cron_directory=cron_directory,
        schedule=schedule,
        dry_run=dry_run,
    )
