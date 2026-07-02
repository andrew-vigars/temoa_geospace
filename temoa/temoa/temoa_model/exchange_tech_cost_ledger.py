"""
Tools for Energy Model Optimization and Analysis (Temoa):
An open source framework for energy systems optimization modeling

Copyright (C) 2015,  NC State University

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

A complete copy of the GNU General Public License v2 (GPLv2) is available
in LICENSE.txt.  Users uncompressing this from an archive may not have
received this license file.  If not, see <http://www.gnu.org/licenses/>.


Written by:  J. F. Hyink
jeff@westernspark.us
https://westernspark.us
Created on:  4/1/24

The purpose of this module is to provide a ledger for all costs for exchange techs.  The main reason
for the need is that in many cases, the costs need to be apportioned by use ratio so it is helpful to
separately gather all of the costs and then use a usage ratio to generate entries when asked for

"""
from collections import defaultdict
from enum import unique, Enum
from typing import Union

from pyomo.common.numeric_types import value

from temoa.temoa_model.temoa_model import TemoaModel
from tests.utilities.namespace_mock import Namespace


@unique
class CostType(Enum):
    INVEST = 1
    FIXED = 2
    VARIABLE = 3
    EMISS = 4
    D_INVEST = 5
    D_FIXED = 6
    D_VARIABLE = 7
    D_EMISS = 8


class ExchangeTechCostLedger:
    def __init__(self, M: Union[TemoaModel, 'Namespace']) -> None:
        self.cost_records: dict[CostType, dict] = defaultdict(dict)
        # could be a Namespace for testing purposes...  See the related test
        self.M = M

    def add_cost_record(self, link: str, period, tech, vintage, cost: float, cost_type: CostType):
        """
        add a cost associated with an exchange tech
        :return:
        """
        r1, r2 = link.split('-')
        if not r1 and r2:
            raise ValueError(f'problem splitting region-region: {link}')
        # add to the "seen" records for appropriate cost type
        self.cost_records[cost_type][r1, r2, tech, vintage, period] = cost

    def get_use_ratio(self, exporter, importer, period, tech, vintage) -> float:
        """
        use flow to calculate the use ratio for these 2 entities for cost apportioning purposes
        :param exporter:
        :param importer:
        :param period:
        :param tech:
        :param vintage:
        :return: the proportion to assign to the IMPORTER, or 0.5 if no usage
        """
        M = self.M
        # need to temporarily reconstitute the names
        rr1 = '-'.join([exporter, importer])
        rr2 = '-'.join([importer, exporter])
        if any(
            (
                period >= vintage + value(M.LifetimeProcess[rr1, tech, vintage]),
                period >= vintage + value(M.LifetimeProcess[rr2, tech, vintage]),
                period < vintage,
            )
        ):
            raise ValueError('received a bogus cost for an illegal period.')
        if tech not in M.tech_annual:
            act_dir1 = value(
                sum(
                    M.V_FlowOut[rr1, period, s, d, S_i, tech, vintage, S_o]
                    for s in M.TimeSeason[period]
                    for d in M.time_of_day
                    for S_i in M.processInputs[rr1, period, tech, vintage]
                    for S_o in M.processOutputsByInput[rr1, period, tech, vintage, S_i]
                )
            )
            act_dir2 = value(
                sum(
                    M.V_FlowOut[rr2, period, s, d, S_i, tech, vintage, S_o]
                    for s in M.TimeSeason[period]
                    for d in M.time_of_day
                    for S_i in M.processInputs[rr2, period, tech, vintage]
                    for S_o in M.processOutputsByInput[rr2, period, tech, vintage, S_i]
                )
            )
        else:
            act_dir1 = value(
                sum(
                    M.V_FlowOutAnnual[rr1, period, S_i, tech, vintage, S_o]
                    for S_i in M.processInputs[rr1, period, tech, vintage]
                    for S_o in M.processOutputsByInput[rr1, period, tech, vintage, S_i]
                )
            )
            act_dir2 = value(
                sum(
                    M.V_FlowOutAnnual[rr2, period, S_i, tech, vintage, S_o]
                    for S_i in M.processInputs[rr2, period, tech, vintage]
                    for S_o in M.processOutputsByInput[rr2, period, tech, vintage, S_i]
                )
            )

        if act_dir1 + act_dir2 > 0:
            return act_dir1 / (act_dir1 + act_dir2)
        return 0.5

    def get_entries(self) -> dict:
        region_costs = defaultdict(dict)

        def add_region_cost(key, cost_type, amount):
            region_costs[key][cost_type] = (
                region_costs[key].get(cost_type, 0.0) + amount
            )

        # Iterate through each region pairing, pull the cost records,
        # and decide if/how to split each one.
        for cost_type in self.cost_records:

            # Make a copy; this is a destructive operation.
            records = self.cost_records[cost_type].copy()

            while records:
                (r1, r2, tech, vintage, period), cost = records.popitem()

                # Try to get the reversed-region partner.
                partner_cost = records.pop(
                    (r2, r1, tech, vintage, period),
                    None,
                )

                if partner_cost is not None:
                    # Both directions have explicit costs.
                    # Record each side without use-ratio splitting.
                    add_region_cost(
                        (r2, period, tech, vintage),
                        cost_type,
                        cost,
                    )
                    add_region_cost(
                        (r1, period, tech, vintage),
                        cost_type,
                        partner_cost,
                    )

                else:
                    # Only one side has a cost.
                    # Split based on use ratio.
                    use_ratio = self.get_use_ratio(
                        r1,
                        r2,
                        period,
                        tech,
                        vintage,
                    )

                    add_region_cost(
                        (r1, period, tech, vintage),
                        cost_type,
                        cost * (1.0 - use_ratio),
                    )
                    add_region_cost(
                        (r2, period, tech, vintage),
                        cost_type,
                        cost * use_ratio,
                    )

        return region_costs