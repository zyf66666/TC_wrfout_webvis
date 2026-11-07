# processed_v3 NetCDF4 schema

## Scope and paths

Each experiment, domain, and variable is stored in exactly one NetCDF4/HDF5
file:

```text
/Volumes/land_1/processed_v3/<experiment>/d<domain:02d>/<variable>.nc
```

Center schemes (2026-11-02, `center_schemes.json`): for any scheme other than
`base`, variables that depend on a storm center (`slp_center`, `layer_center`,
or anything that depends on them through the registry graph) are stored under

```text
/Volumes/land_1/processed_v3/centers/<scheme>/<experiment>/d<domain:02d>/<variable>.nc
```

with `centers/<scheme>/scheme.json` recording the scheme's center options (immutable once
written). Center-independent variables stay at the shared path above and are reused by all
schemes. Such files carry a global attribute `center_scheme`, and each slice's
`request_options.center_scheme` names the scheme (absent means `base`).

The adjacent `<variable>.nc.lock` file is an advisory process lock and contains
no scientific data. Existing `处理数据`, `处理数据_v2`, `processed`, and
`processed_v2` files are never modified. Core v3 processing does not import or
execute either old package; only the optional migration/comparison tools read
old NPY results when explicitly invoked.

## Time and height coordinates

- `record` is unlimited.
- `time_minutes(record)` is signed `int64`, unique, and stores whole minutes
  after the file attribute `time_origin`.
- WRF source indexing uses `START_DATE` as `time_origin` for this project.
- Physical record order may reflect append order. Readers must reject duplicate
  `time_minutes` and return records sorted by `time_minutes`.
- `height` is unlimited.
- `height_m(height)` is signed `int32`, unique, and stores whole metres.
- A new height-aware file starts with the 66 standard levels: 100--1000 m at
  100 m intervals and 1250--15000 m at 250 m intervals.
- Other integer heights may be appended. Readers select and sort by coordinate
  value rather than assuming that coordinate storage order is sorted.

## Data layouts

| Layout | `data` dimensions | `completed` dimensions |
|---|---|---|
| `cartesian_3d` | `(record, height, y, x)` | `(record, height)` |
| `polar_3d` | `(record, height, azimuth, radius)` | `(record, height)` |
| `cartesian_2d` | `(record, y, x)` | `(record)` |
| `polar_2d` | `(record, azimuth, radius)` | `(record)` |
| `scalar` | `(record)` | `(record)` |
| `height_scalar` | `(record, height)` | `(record, height)` |
| `vector` | `(record, component)` | `(record)` |
| `height_vector` | `(record, height, component)` | `(record, height)` |

`data` is always IEEE `float32`. `completed` is `uint8`, where 0 means missing
or interrupted and 1 means complete. Coordinate variables use integer or
`float32` types appropriate to their physical meaning.

Polar files contain `azimuth_deg(azimuth)` and `radius_km(radius)`. Cartesian
files contain integer `y_index(y)` and `x_index(x)`. Component files contain a
string `component_name(component)` coordinate.

Height-aware files may also contain an optional `lowest` group for tasks whose
`height_m` is `"lowest"`. This means model level index 0, whose physical height
varies across the grid; it is separate from the integer-metre height axis.
The group has attributes `vertical_level="lowest_model_level"` and
`model_level_index=0`. Its `data` uses the root data dimensions with `height`
removed, while `completed` and `metadata_json` each use `(record)`.
It shares the root time and spatial coordinates, compression settings, lock,
and completion protocol. A lowest-level write can add this group to an existing
file without changing physical-height slices. Missing groups and incomplete
lowest-level records are unavailable and may be computed on the next run.

The spatial coordinate contract is immutable after file creation. A later
write with a different grid shape, azimuth coordinate, radius coordinate, or
component list fails rather than mixing incompatible slices in one variable
file.

## Compression and chunking

The `data` variable uses:

```text
dtype=float32
zlib=True
complevel=2
shuffle=True
fletcher32=True
_FillValue=NaN
```

Chunks always isolate a single record and, for height-aware data, a single
height. Cartesian chunks use at most 256 x 256 spatial points. Polar chunks use
at most 90 azimuths x 256 radii. This makes a single time/height read independent
of the total logical file size.

`completed` and `metadata_json` use `(1)` or `(1, 1)` chunks so their automatic
HDF5 chunk choice cannot allocate multi-megabyte empty auxiliary chunks.

Unwritten standard heights do not allocate data chunks and are unavailable
because their `completed` value remains 0.

When a requested polar diagnostic depends on `layer_center`, the planner fills
the standard center-height chain through the highest requested height. This
retains the established vertically continuous center without writing the
target diagnostic at unrequested heights.

## Completion protocol and locking

All processes that access a variable use `<variable>.nc.lock`. Writers take an
exclusive `fcntl.flock`; readers take a shared lock. A writer performs these
steps while holding the exclusive lock:

1. Re-open and validate the file and coordinate uniqueness.
2. Re-check whether the requested slice is already complete.
3. Locate or append coordinate values.
4. Set the target `completed` value to 0 and call `sync()`.
5. Write `data` and `metadata_json`, then call `sync()`.
6. Set `completed` to 1 and call `sync()` again.

Readers never return a slice whose `completed` value is not 1. An interrupted
slice can be overwritten on the next run. Existing complete slices remain
valid if a forced recomputation fails before it starts writing.

## Metadata and provenance

Stable attributes include at least:

- `schema_version`
- `experiment`
- `domain`
- `variable`
- `layout`
- `units`
- `time_origin`
- `algorithm_version`

Per-slice canonical JSON is stored in `metadata_json(record[, height])`. It may
contain source file and record, source and dependency fingerprints, request and
resolved options, grid spacing, environmental reference values, and algorithm
parameters. JSON objects are written with sorted keys.

## Read rules and v2 fallback

The v3 API exposes coordinate-based slice reads and streaming iteration. It
does not expose a convenience operation that materialises `data[:]`.

The Web resolver checks availability per requested slice:

1. Use the v3 NetCDF slice when its coordinates exist and `completed == 1`.
2. Otherwise try the corresponding `processed_v2` NPY file.
3. Report the input missing only when neither source is available.

This permits a plot to use a mixture of migrated/computed v3 slices and v2
fallback slices without reading a complete multi-gigabyte NetCDF variable.
