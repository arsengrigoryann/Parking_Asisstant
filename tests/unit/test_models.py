"""Tests for the operational parking domain model."""

from typing import cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from parking_assistant.db.models import (
    Base,
    ParkingFacility,
    ParkingSpace,
    SpaceStatus,
    SpaceType,
)


def test_space_enums_expose_only_supported_values() -> None:
    assert {item.value for item in SpaceType} == {
        "regular",
        "accessible",
        "ev",
        "motorcycle",
    }
    assert {item.value for item in SpaceStatus} == {
        "available",
        "occupied",
        "out_of_service",
    }


def test_facility_space_relationship_is_bidirectional() -> None:
    facility = ParkingFacility(
        name="Test Parking",
        address="1 Test Street",
        timezone="Asia/Yerevan",
    )
    space = ParkingSpace(
        space_number="P-01",
        space_type=SpaceType.REGULAR,
        status=SpaceStatus.AVAILABLE,
    )

    facility.spaces.append(space)

    assert space.facility is facility
    assert space in facility.spaces


def test_database_mapping_rejects_unknown_space_type() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    facility = ParkingFacility(
        name="Test Parking",
        address="1 Test Street",
        timezone="Asia/Yerevan",
    )
    facility.spaces.append(
        ParkingSpace(
            space_number="P-01",
            space_type=cast(SpaceType, "spaceship"),
            status=SpaceStatus.AVAILABLE,
        )
    )

    with Session(engine) as session, pytest.raises(StatementError):
        session.add(facility)
        session.commit()

