"""National gasoline deployment policy using standard Temoa v4 constraints."""
import math

import pandas as pd


CONVENTIONAL_GROUP = 'GSL_CONVENTIONAL_POLICY'


def rebuild_national_synthetic_share(db: dict, period: int, share: float) -> None:
    """Cap conventional annual supply at (1-share) times fixed annual demand.

    In the current closed gasoline system, all remaining demand is served by
    synthesis. Count backup supply as conventional so synthetic production
    lost in delivery does not earn credit.
    """
    if not math.isfinite(share) or not 0 <= share <= 1:
        raise ValueError('National synthetic gasoline share must be finite and in [0, 1]')
    for table, column in [('limit_activity', 'tech_or_group'),
                          ('tech_group_member', 'group_name'), ('tech_group', 'group_name')]:
        db[table] = db[table].loc[db[table][column].ne(CONVENTIONAL_GROUP)].copy()
    if share == 0:
        return
    demand = db['demand'].loc[db['demand'].period.eq(period) & db['demand'].commodity.eq('d_gsl'), 'demand']
    if demand.empty or not demand.map(lambda v: math.isfinite(v) and v > 0).all():
        raise ValueError('Positive national share requires finite positive annual gasoline demand')
    routes = db['efficiency'].loc[db['efficiency'].tech.eq('GSL_BACKUP')]
    # The conventional route currently delivers unit-efficiency gasoline at
    # demand nodes. Reject future route changes rather than miscount losses.
    if not routes.efficiency.eq(1).all() or not routes.output_comm.eq('d_gsl').all():
        raise ValueError('National share requires unit-efficiency conventional gasoline delivery')
    techs = sorted(set(routes.tech))
    if not techs:
        raise ValueError('No conventional gasoline routes available for national share accounting')
    def append(table, rows):
        extra = pd.DataFrame(rows).reindex(columns=db[table].columns)
        db[table] = extra if db[table].empty else pd.concat([db[table], extra], ignore_index=True)
    append('tech_group', [dict(group_name=CONVENTIONAL_GROUP, notes='Conventional gasoline; excluded from synthetic share')])
    append('tech_group_member', [dict(group_name=CONVENTIONAL_GROUP, tech=t) for t in techs])
    append('limit_activity', [dict(region='global', period=period,
        tech_or_group=CONVENTIONAL_GROUP, operator='le', activity=(1-share)*float(demand.sum()),
        units='t gasoline/year', notes=f'National synthetic minimum {share:g}; annual demand basis; no city minimum')])
