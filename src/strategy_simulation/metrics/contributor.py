from grid_metrics import GridMetricInputContributor

from ..experiment_provider import STRATEGIES_SIMULATION_PROVIDER_V1


class StrategiesCoinMMetricInputContributor(GridMetricInputContributor):
    contributor_name = "strategies-coinm-account-series"
    provider_id = STRATEGIES_SIMULATION_PROVIDER_V1
