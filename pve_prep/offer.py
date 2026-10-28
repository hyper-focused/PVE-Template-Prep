# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
"""Offer a cron schedule for templates this run created.

An empty created list asks nothing. Enter, or no, leaves the host unchanged.
Weekly or monthly writes one enrollment per new template and installs one
cron script. A dry run prints that script and those records instead.
"""

from __future__ import annotations

import json

from pve_prep.cronscript import CronScriptError, cron_script, install_cron_script
from pve_prep.enroll import Enrollment, save_enrollment
from pve_prep.prompts import PromptAbort, ask_unattended_schedule


def _record_json(record: Enrollment) -> str:
    """One JSON object, same fields as the enrollment file."""
    payload = {
        "vmid": record.vmid,
        "distro": record.distro,
        "release": record.release,
        "digest": record.digest,
        "checksum_alg": record.checksum_alg,
        "password_auth": record.password_auth,
        "permit_root": record.permit_root,
        "schedule": record.schedule,
    }
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def offer_created(
    read_line,
    write,
    *,
    created: list[tuple[str, int]],
    distro: str,
    password_auth: bool,
    permit_root: bool,
    digest_for,
    program: str,
    directory,
    cron_directory,
    dry_run: bool = False,
) -> str | None:
    """Ask, then enroll templates this run created and converted.

    created is (release, vmid) pairs. None means nothing was offered or the
    operator declined. weekly or monthly is the schedule they chose, including
    when a digest or a save failed. PromptAbort propagates.
    """
    if not created:
        return None
    schedule = ask_unattended_schedule(read_line, write)
    if schedule is None:
        return None
    for release, vmid in created:
        try:
            digest, checksum_alg = digest_for(distro, release)
            record = Enrollment(
                vmid=vmid,
                distro=distro,
                release=release,
                digest=digest,
                checksum_alg=checksum_alg,
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
            continue
        if dry_run:
            write(_record_json(record))
    if dry_run:
        try:
            write(cron_script(program, schedule))
        except CronScriptError as exc:
            write(f"{exc}\n")
        return schedule
    try:
        install_cron_script(program, schedule, directory=cron_directory)
    except CronScriptError as exc:
        write(f"{exc}\n")
    return schedule
