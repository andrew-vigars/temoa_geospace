# What is GeoCANOE?

GeoCANOE extends the CANOE/Temoa modelling stack with explicit Canadian spatial
pre-processing. It turns public boundaries, transportation networks, demand,
emissions, geological storage, weather, land eligibility, technology and cost
data into a spatially resolved Temoa database.

GeoCANOE is both a data pipeline and a model-construction layer. It decides
which model regions, links, technologies, limits and costs are encoded. Temoa
then constructs and solves the optimization problem from that database.

The workflow uses four durable data layers:

1. **Bronze** preserves acquired external inputs and their provenance.
2. **Silver** creates reusable spatial, resource and cost evidence products.
3. **Gold** maps those products and controlled assumptions into the Temoa schema.
4. **Runs** solve an isolated Gold copy and archive configuration, provenance,
   diagnostics and results.

Start with [GeoCANOE, CANOE and Temoa](canoe-and-temoa.md) for the ownership
boundary, then read [Model architecture](model-architecture.md) for the pipeline.
