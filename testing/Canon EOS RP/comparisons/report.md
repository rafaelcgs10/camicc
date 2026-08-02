# Canon EOS RP — dcp2icc comparison suite

DCP: `Canon EOS RP Camera Standard.dcp` — 4 image(s), tone mapper: sigmoid. Mean absolute pixel difference vs the out-of-camera JPEG (0–255, lower is better).

## Aggregate (average over all images)

| Rendering | mean diff | p95 | images |
|---|---|---|---|
| RawTherapee (native DCP) | 9.1 | 24 | 4 |
| dcp2icc (camera look) | 9.9 | 27 | 4 |
| darktable default (sigmoid) | 11.0 | 24 | 4 |
| dcp2icc (colors only)+sigmoid | 12.3 | 27 | 4 |

## IMG_8736

| Rendering | mean diff | p95 |
|---|---|---|
| dcp2icc (camera look) | 9.3 | 35 |
| RawTherapee (native DCP) | 12.1 | 37 |
| dcp2icc (colors only)+sigmoid | 12.6 | 32 |
| darktable default (sigmoid) | 12.8 | 32 |

![IMG_8736](IMG_8736/comparison-full.jpg)

## IMG_8919

| Rendering | mean diff | p95 |
|---|---|---|
| RawTherapee (native DCP) | 7.1 | 18 |
| darktable default (sigmoid) | 10.3 | 23 |
| dcp2icc (colors only)+sigmoid | 11.8 | 29 |
| dcp2icc (camera look) | 11.9 | 29 |

![IMG_8919](IMG_8919/comparison-full.jpg)

## IMG_9029

| Rendering | mean diff | p95 |
|---|---|---|
| darktable default (sigmoid) | 10.3 | 26 |
| RawTherapee (native DCP) | 12.4 | 30 |
| dcp2icc (camera look) | 13.5 | 28 |
| dcp2icc (colors only)+sigmoid | 14.1 | 32 |

![IMG_9029](IMG_9029/comparison-full.jpg)

## IMG_9399

| Rendering | mean diff | p95 |
|---|---|---|
| dcp2icc (camera look) | 4.8 | 15 |
| RawTherapee (native DCP) | 5.0 | 9 |
| darktable default (sigmoid) | 10.5 | 16 |
| dcp2icc (colors only)+sigmoid | 10.6 | 16 |

![IMG_9399](IMG_9399/comparison-full.jpg)
