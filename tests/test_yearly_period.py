"""Tests for schedules whose period repeats every year."""

import datetime

import pytest
from homeassistant.const import STATE_ON
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry, async_fire_time_changed)

from custom_components.scheduler import const
from custom_components.scheduler.timer import yearly_period

TIME_ZONE = "Europe/Madrid"

WINTER = {
    const.ATTR_START_DATE: "2026-11-01",
    const.ATTR_END_DATE: "2027-03-31",
    const.ATTR_REPEAT_YEARLY: True,
}


@pytest.mark.parametrize(
    ("start", "end", "day", "expected"),
    [
        # a period within one year
        ("2020-06-01", "2020-08-31", "2026-07-15", ("2026-06-01", "2026-08-31")),
        ("2020-06-01", "2020-08-31", "2026-03-01", ("2026-06-01", "2026-08-31")),
        ("2020-06-01", "2020-08-31", "2026-09-01", ("2027-06-01", "2027-08-31")),
        ("2020-06-01", "2020-08-31", "2026-08-31", ("2026-06-01", "2026-08-31")),
        # a period that wraps around the new year
        ("2026-11-01", "2027-03-31", "2026-12-15", ("2026-11-01", "2027-03-31")),
        ("2026-11-01", "2027-03-31", "2027-02-10", ("2026-11-01", "2027-03-31")),
        ("2026-11-01", "2027-03-31", "2027-07-15", ("2027-11-01", "2028-03-31")),
        ("2026-11-01", "2027-03-31", "2027-04-01", ("2027-11-01", "2028-03-31")),
        # February 29
        ("2024-02-29", "2024-03-05", "2027-02-01", ("2027-02-28", "2027-03-05")),
        ("2024-02-29", "2024-03-05", "2028-02-01", ("2028-02-29", "2028-03-05")),
        ("2024-02-01", "2024-02-29", "2027-02-28", ("2027-02-01", "2027-02-28")),
        ("2024-02-01", "2024-02-29", "2027-03-01", ("2028-02-01", "2028-02-29")),
        # a single day
        ("2020-12-25", "2020-12-25", "2026-12-26", ("2027-12-25", "2027-12-25")),
    ],
)
def test_yearly_period(start, end, day, expected):
    """The period containing a day, or else the next one, is found."""
    assert yearly_period(start, end, datetime.date.fromisoformat(day)) == expected


def local(value: str) -> datetime.datetime:
    """Return a timestamp in the test time zone."""
    return datetime.datetime.fromisoformat(value).replace(
        tzinfo=dt_util.get_time_zone(TIME_ZONE)
    )


async def move_to(hass, freezer, time: datetime.datetime):
    """Move the clock and let the timers that are due fire."""
    freezer.move_to(time)
    async_fire_time_changed(hass, time)
    await hass.async_block_till_done()


async def setup_scheduler(hass, freezer, now: str):
    """Set up the Scheduler integration and wait until it is ready."""
    await hass.config.async_set_time_zone(TIME_ZONE)
    freezer.move_to(local(now))
    hass.services.async_register("test", "turn_on", lambda call: None)
    entry = MockConfigEntry(domain=const.DOMAIN, version=2)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await move_to(hass, freezer, local(now) + datetime.timedelta(seconds=10))


async def add_schedule(hass, **data) -> str:
    """Add a schedule with a single timeslot and return its entity ID."""
    before = set(hass.states.async_entity_ids("switch"))
    await hass.services.async_call(
        const.DOMAIN,
        const.SERVICE_ADD,
        {
            const.ATTR_WEEKDAYS: ["daily"],
            const.ATTR_TIMESLOTS: [
                {
                    const.ATTR_START: "06:30",
                    const.ATTR_STOP: "07:00",
                    const.ATTR_ACTIONS: [{"service": "test.turn_on"}],
                }
            ],
            const.ATTR_REPEAT_TYPE: const.REPEAT_TYPE_PAUSE,
            **data,
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    (entity_id,) = set(hass.states.async_entity_ids("switch")) - before
    return entity_id


def next_trigger(hass, entity_id: str) -> datetime.datetime:
    """Return the next trigger of a schedule."""
    return dt_util.parse_datetime(hass.states.get(entity_id).attributes["next_trigger"])


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-07-15 12:00", "2026-11-01 06:30"),
        ("2027-02-10 12:00", "2027-02-11 06:30"),
        ("2027-03-31 12:00", "2027-11-01 06:30"),
        ("2030-12-31 12:00", "2031-01-01 06:30"),
    ],
)
async def test_next_trigger_follows_the_yearly_period(
    hass, freezer, enable_custom_integrations, now, expected
):
    """Outside the period the schedule waits for the next one."""
    await setup_scheduler(hass, freezer, now)
    entity_id = await add_schedule(hass, **WINTER)

    assert next_trigger(hass, entity_id) == local(expected)
    assert hass.states.get(entity_id).state == STATE_ON


async def test_period_without_repeat_completes(
    hass, freezer, enable_custom_integrations
):
    """Without the flag, a period in the past has no next trigger to wait for."""
    await setup_scheduler(hass, freezer, "2027-04-15 12:00")
    entity_id = await add_schedule(hass, **{**WINTER, const.ATTR_REPEAT_YEARLY: False})

    assert next_trigger(hass, entity_id) < local("2027-04-15 12:00")
    assert hass.states.get(entity_id).state == const.STATE_COMPLETED


@pytest.mark.parametrize(
    ("repeat_yearly", "state"),
    [(True, STATE_ON), (False, const.STATE_COMPLETED)],
)
async def test_last_day_of_the_period(
    hass, freezer, enable_custom_integrations, repeat_yearly, state
):
    """Only a yearly schedule stays on after the last timeslot of its period."""
    await setup_scheduler(hass, freezer, "2027-03-31 06:00")
    entity_id = await add_schedule(
        hass, **{**WINTER, const.ATTR_REPEAT_YEARLY: repeat_yearly}
    )

    # timers fire just after their time, not at the frozen instant itself
    for time in ["2027-03-31 06:30:01", "2027-03-31 06:31:01", "2027-03-31 07:00:01"]:
        await move_to(hass, freezer, local(time))

    assert hass.states.get(entity_id).state == state


async def test_edit_without_the_flag_keeps_it(
    hass, freezer, enable_custom_integrations
):
    """A client that doesn't know the flag doesn't clear it when editing."""
    await setup_scheduler(hass, freezer, "2026-07-15 12:00")
    entity_id = await add_schedule(hass, **WINTER)

    await hass.services.async_call(
        const.DOMAIN,
        const.SERVICE_EDIT,
        {
            "entity_id": entity_id,
            const.ATTR_START_DATE: WINTER[const.ATTR_START_DATE],
            const.ATTR_END_DATE: WINTER[const.ATTR_END_DATE],
            const.ATTR_REPEAT_TYPE: const.REPEAT_TYPE_REPEAT,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    (schedule,) = hass.data[const.DOMAIN]["coordinator"].async_get_schedules()
    assert schedule[const.ATTR_REPEAT_YEARLY] is True
    assert next_trigger(hass, entity_id) == local("2026-11-01 06:30")
