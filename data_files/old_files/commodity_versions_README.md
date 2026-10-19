# Legacy model inputs

These files are historical references and are not active registry inputs.

- `commodities_v1.csv`: original commodity table with `wa` for hydrogen,
  methanol, gasoline, and electricity.
- `commodities_v1_carbon.csv`: v1 flags with the carbon-accounting definitions
  added for the completed v1/v2 comparison.

The tested v2 definitions are now standardized as `registry/commodities.csv`.
The Bronze registry and Gold builder use that canonical file. Completed model
artifacts retain their original input snapshots and recorded source paths;
this archive does not alter those results.
