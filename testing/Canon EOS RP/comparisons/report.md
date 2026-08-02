# Canon EOS RP — dcp2icc comparison suite

DCP: `Canon EOS RP Camera Standard.dcp` — 4 image(s), tone mapper: sigmoid. Mean absolute pixel difference vs the out-of-camera JPEG (0–255, lower is better).

## Aggregate (average over all images)

| Rendering | mean diff | p95 | images |
|---|---|---|---|
| RawTherapee (native DCP) | 10.9 | 27 | 4 |
| dcp2icc (camera look) | 17.6 | 48 | 4 |
| darktable default (sigmoid) | 18.9 | 46 | 4 |
| dcp2icc (colors only)+sigmoid | 19.6 | 46 | 4 |

## IMG_8736

| Rendering | mean diff | p95 |
|---|---|---|
| RawTherapee (native DCP) | 13.2 | 35 |
| dcp2icc (camera look) | 17.4 | 50 |
| dcp2icc (colors only)+sigmoid | 18.9 | 46 |
| darktable default (sigmoid) | 19.4 | 46 |

![IMG_8736](IMG_8736/comparison-full.jpg)

## IMG_8919

| Rendering | mean diff | p95 |
|---|---|---|
| RawTherapee (native DCP) | 9.2 | 24 |
| darktable default (sigmoid) | 16.2 | 57 |
| dcp2icc (colors only)+sigmoid | 16.6 | 56 |
| dcp2icc (camera look) | 17.0 | 60 |

![IMG_8919](IMG_8919/comparison-full.jpg)

## IMG_9029

| Rendering | mean diff | p95 |
|---|---|---|
| RawTherapee (native DCP) | 15.0 | 36 |
| darktable default (sigmoid) | 20.8 | 47 |
| dcp2icc (camera look) | 23.5 | 50 |
| dcp2icc (colors only)+sigmoid | 23.9 | 47 |

![IMG_9029](IMG_9029/comparison-full.jpg)

## IMG_9399

| Rendering | mean diff | p95 |
|---|---|---|
| RawTherapee (native DCP) | 6.2 | 13 |
| dcp2icc (camera look) | 12.6 | 30 |
| darktable default (sigmoid) | 19.1 | 34 |
| dcp2icc (colors only)+sigmoid | 19.1 | 34 |

![IMG_9399](IMG_9399/comparison-full.jpg)
