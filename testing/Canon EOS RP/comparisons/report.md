# Canon EOS RP — dcp2icc comparison suite

DCP: auto-matched per image from the camera model and Picture Style — 5 image(s), tone mapper: sigmoid. Mean absolute pixel difference on the central 80% of the frame (0–255, lower is better), against each available source of truth.

## Aggregate vs Camera JPEG (Auto)

| Rendering | mean diff | p95 | images |
|---|---|---|---|
| Lightroom | 7.6 | 13 | 1 |
| dcp2icc (camera look) | 7.9 | 17 | 1 |
| darktable default (sigmoid) | 13.4 | 23 | 1 |
| dcp2icc (colors only)+sigmoid | 13.7 | 23 | 1 |
| RawTherapee (native DCP) | 17.1 | 27 | 1 |

## Aggregate vs Lightroom

| Rendering | mean diff | p95 | images |
|---|---|---|---|
| Camera JPEG (Auto) | 7.6 | 13 | 1 |
| Camera JPEG (Standard) | 8.2 | 23 | 4 |
| dcp2icc (camera look) | 8.9 | 16 | 5 |
| RawTherapee (native DCP) | 12.1 | 23 | 5 |
| darktable default (sigmoid) | 12.3 | 24 | 5 |
| dcp2icc (colors only)+sigmoid | 13.1 | 25 | 5 |

## Aggregate vs Camera JPEG (Standard)

| Rendering | mean diff | p95 | images |
|---|---|---|---|
| Lightroom | 8.2 | 23 | 4 |
| RawTherapee (native DCP) | 9.1 | 24 | 4 |
| dcp2icc (camera look) | 9.9 | 27 | 4 |
| darktable default (sigmoid) | 11.0 | 24 | 4 |
| dcp2icc (colors only)+sigmoid | 12.3 | 27 | 4 |

## 19-43-22-103

### vs Camera JPEG (Auto)

| Rendering | mean diff | p95 |
|---|---|---|
| Lightroom | 7.6 | 13 |
| dcp2icc (camera look) | 7.9 | 17 |
| darktable default (sigmoid) | 13.4 | 23 |
| dcp2icc (colors only)+sigmoid | 13.7 | 23 |
| RawTherapee (native DCP) | 17.1 | 27 |

![19-43-22-103 vs Camera JPEG (Auto)](19-43-22-103/comparison-full.jpg)

### vs Lightroom

| Rendering | mean diff | p95 |
|---|---|---|
| Camera JPEG (Auto) | 7.6 | 13 |
| RawTherapee (native DCP) | 10.0 | 19 |
| dcp2icc (camera look) | 14.6 | 19 |
| darktable default (sigmoid) | 20.5 | 28 |
| dcp2icc (colors only)+sigmoid | 20.9 | 27 |

![19-43-22-103 vs Lightroom](19-43-22-103/comparison-lightroom.jpg)

## IMG_8736

### vs Camera JPEG (Standard)

| Rendering | mean diff | p95 |
|---|---|---|
| dcp2icc (camera look) | 9.3 | 35 |
| RawTherapee (native DCP) | 12.1 | 37 |
| dcp2icc (colors only)+sigmoid | 12.6 | 32 |
| darktable default (sigmoid) | 12.8 | 32 |
| Lightroom | 13.8 | 44 |

![IMG_8736 vs Camera JPEG (Standard)](IMG_8736/comparison-full.jpg)

### vs Lightroom

| Rendering | mean diff | p95 |
|---|---|---|
| dcp2icc (camera look) | 6.4 | 15 |
| dcp2icc (colors only)+sigmoid | 9.3 | 20 |
| darktable default (sigmoid) | 9.7 | 20 |
| Camera JPEG (Standard) | 13.8 | 44 |
| RawTherapee (native DCP) | 15.5 | 26 |

![IMG_8736 vs Lightroom](IMG_8736/comparison-lightroom.jpg)

## IMG_8919

### vs Camera JPEG (Standard)

| Rendering | mean diff | p95 |
|---|---|---|
| Lightroom | 5.3 | 16 |
| RawTherapee (native DCP) | 7.1 | 18 |
| darktable default (sigmoid) | 10.3 | 23 |
| dcp2icc (colors only)+sigmoid | 11.8 | 29 |
| dcp2icc (camera look) | 11.9 | 29 |

![IMG_8919 vs Camera JPEG (Standard)](IMG_8919/comparison-full.jpg)

### vs Lightroom

| Rendering | mean diff | p95 |
|---|---|---|
| Camera JPEG (Standard) | 5.3 | 16 |
| RawTherapee (native DCP) | 8.0 | 20 |
| dcp2icc (camera look) | 10.1 | 19 |
| darktable default (sigmoid) | 10.7 | 26 |
| dcp2icc (colors only)+sigmoid | 11.8 | 27 |

![IMG_8919 vs Lightroom](IMG_8919/comparison-lightroom.jpg)

## IMG_9029

### vs Camera JPEG (Standard)

| Rendering | mean diff | p95 |
|---|---|---|
| Lightroom | 6.2 | 15 |
| darktable default (sigmoid) | 10.3 | 26 |
| RawTherapee (native DCP) | 12.4 | 30 |
| dcp2icc (camera look) | 13.5 | 28 |
| dcp2icc (colors only)+sigmoid | 14.1 | 32 |

![IMG_9029 vs Camera JPEG (Standard)](IMG_9029/comparison-full.jpg)

### vs Lightroom

| Rendering | mean diff | p95 |
|---|---|---|
| Camera JPEG (Standard) | 6.2 | 15 |
| dcp2icc (camera look) | 9.5 | 22 |
| darktable default (sigmoid) | 10.2 | 26 |
| dcp2icc (colors only)+sigmoid | 13.4 | 32 |
| RawTherapee (native DCP) | 15.8 | 32 |

![IMG_9029 vs Lightroom](IMG_9029/comparison-lightroom.jpg)

## IMG_9399

### vs Camera JPEG (Standard)

| Rendering | mean diff | p95 |
|---|---|---|
| dcp2icc (camera look) | 4.8 | 15 |
| RawTherapee (native DCP) | 5.0 | 9 |
| Lightroom | 7.6 | 17 |
| darktable default (sigmoid) | 10.5 | 16 |
| dcp2icc (colors only)+sigmoid | 10.6 | 16 |

![IMG_9399 vs Camera JPEG (Standard)](IMG_9399/comparison-full.jpg)

### vs Lightroom

| Rendering | mean diff | p95 |
|---|---|---|
| dcp2icc (camera look) | 3.8 | 7 |
| Camera JPEG (Standard) | 7.6 | 17 |
| dcp2icc (colors only)+sigmoid | 10.0 | 17 |
| darktable default (sigmoid) | 10.3 | 18 |
| RawTherapee (native DCP) | 11.4 | 19 |

![IMG_9399 vs Lightroom](IMG_9399/comparison-lightroom.jpg)
