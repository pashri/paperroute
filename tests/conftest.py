"""Give the repository's own tests Sydney as the city under test."""

from paperroute_sydney.asset_root import APP_ASSETS
from paperroute_sydney.city import SYDNEY
from paperroute_testkit.city_setting import use_city_under_test

use_city_under_test(city=SYDNEY, assets=APP_ASSETS)
