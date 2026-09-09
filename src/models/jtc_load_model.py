# -*- coding: utf-8 -*-
"""JTC common load -- the landlord / common-services demand on the diagram.

Physically it behaves exactly like any other CSV-driven load, so the profile
logic is inherited from LoadModel unchanged. It exists as its own type for one
reason: it sits *outside* the Tower 10 control loop. MeterModel only sums the
types it knows about (PV, BESS, EV, Load), so giving this device a distinct
type keeps it out of the T98 point of common coupling while still letting the
virtual grid meter above the EGC control boundary see it.
"""
import logging

from src.models.load_model import LoadModel

logger = logging.getLogger('state')


class JTCLoadModel(LoadModel):
    def update(self):
        # Same curve interpolation as LoadModel, published under its own point
        # name so the two loads never collide in the register map.
        return {'JTC.Power': super().update()['Load.Power']}
