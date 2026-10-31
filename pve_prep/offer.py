# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Enrollment offers for create and disk-image upgrade runs.

Before any download, selected VMIDs that already have a record are asked
keep / change schedule / turn off. That choice is stored for the run and
applied only to builds that succeed. After the builds, templates that had
no prior record get the normal weekly / monthly / no prompt. Standalone
image jobs skip both asks. Weekly or monthly is one schedule for the host:
every enrollment is moved onto it, that cron script is installed, and the
other schedule's script is removed when we wrote it. When the last record
for a schedule is gone, our cron wrapper for that schedule is removed.
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
    SCHEDULES,
    Enrollment,
    EnrollmentError,
    enrollment_files,
    enrollments_for,
    load_enrollment,
    load_enrollments,
    remove_enrollment,
    save_enrollment,
)
from pve_prep.prompts import (
    PromptAbort,
    ask_prior_enrollment,
    ask_turn_off_updates,
    ask_unattended_schedule,
)

PriorAction = Literal["keep", "off", "weekly", "monthly"]


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
    """Drop our cron wrapper for every schedule that has no enrollments left."""
    for schedule in SCHEDULES:
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


def _move_enrollments_to(schedule: str, *, directory, write, dry_run: bool) -> None:
    """Point every existing enrollment at schedule. One cadence for the host."""
    try:
        records = load_enrollments(directory=directory)
    except EnrollmentError as exc:
        write(f"Existing automated update records could not be read: {exc}\n")
        return
    for record in records:
        if record.schedule == schedule:
            continue
        if dry_run:
            write(
                f"Dry run: VMID {record.vmid} would move from {record.schedule} "
                f"to {schedule} automated updates.\n"
            )
            continue
        try:
            save_enrollment(replace(record, schedule=schedule), directory=directory)
        except EnrollmentError as exc:
            write(f"VMID {record.vmid} could not be moved to {schedule}: {exc}\n")
            continue
        write(
            f"VMID {record.vmid} moved from {record.schedule} to {schedule} "
            "automated updates.\n"
        )


def _drop_other_cron(
    write,
    *,
    program: str,
    schedule: str,
    cron_directory,
    dry_run: bool,
) -> None:
    other = "monthly" if schedule == "weekly" else "weekly"
    if dry_run:
        write(
            f"Dry run: /etc/cron.{other}/pve-template-prep would be removed "
            "if it is still ours.\n"
        )
        return
    try:
        removed = remove_cron_script(program, other, directory=cron_directory)
    except CronScriptError as exc:
        write(f"{exc}\n")
        return
    if removed:
        write(f"Removed the {other} automated-update cron script.\n")


def _install_schedule(
    write,
    *,
    program: str,
    schedule: str,
    cron_directory,
    dry_run: bool,
) -> None:
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


def _forget(write, created: list[tuple[str, int]], *, directory, dry_run: bool) -> None:
    for _release, vmid in created:
        if dry_run:
            write(f"Dry run: any automated update record for VMID {vmid} would be removed.\n")
            continue
        try:
            removed = remove_enrollment(vmid, directory=directory)
        except EnrollmentError as exc:
            write(f"The automated update record for VMID {vmid} could not be removed: {exc}\n")
            continue
        if removed:
            write(f"Automated updates for VMID {vmid} were removed.\n")


def _forget_all(write, *, directory, dry_run: bool) -> None:
    try:
        files = enrollment_files(directory=directory)
    except EnrollmentError as exc:
        write(f"Automated update records could not be read: {exc}\n")
        return
    if dry_run:
        for vmid, _path in files:
            write(f"Dry run: any automated update record for VMID {vmid} would be removed.\n")
        return
    for vmid, _path in files:
        try:
            removed = remove_enrollment(vmid, directory=directory)
        except EnrollmentError as exc:
            write(f"The automated update record for VMID {vmid} could not be removed: {exc}\n")
            continue
        if removed:
            write(f"Automated updates for VMID {vmid} were removed.\n")


def _schedule_for_prior(plan: PriorEnrollmentPlan, vmid: int) -> str:
    """Schedule to write for one prior VMID on keep or change."""
    if plan.action in {"weekly", "monthly"}:
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

    keep or change rewrites digest, storage, and disk_digest. off removes the
    JSON. Cron wrappers with nothing left are removed. created must already be
    filtered to successful prior VMIDs.
    """
    if not created:
        return
    if plan.action == "off":
        _forget(write, created, directory=directory, dry_run=dry_run)
        reconcile_cron_scripts(
            write,
            program=program,
            directory=directory,
            cron_directory=cron_directory,
            dry_run=dry_run,
        )
        return
    if plan.action in {"weekly", "monthly"}:
        _move_enrollments_to(
            plan.action, directory=directory, write=write, dry_run=dry_run
        )
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
        if plan.action in {"weekly", "monthly"}:
            _drop_other_cron(
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
    _forget_all(write, directory=directory, dry_run=dry_run)
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
    _move_enrollments_to(schedule, directory=directory, write=write, dry_run=dry_run)
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
    _drop_other_cron(
        write,
        program=program,
        schedule=schedule,
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
    operator can turn every enrollment off. None means nothing was offered
    or the operator declined. A decline removes nothing for brand-new VMIDs
    that were never enrolled; it only matters when turning updates off.
    weekly or monthly becomes the only schedule on the host. PromptAbort
    propagates and changes no record.
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
    if schedule is None:
        return None
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
