# Sigmoid parameter search — Canon EOS RP

DCP: `Canon EOS RP Camera Standard.dcp`, colors-only profile, exposure +0.7 EV. Mean absolute pixel difference on the central 80% of the frame (0–255, lower is better), per image and averaged, against each available source of truth.

## vs Camera JPEG

| sigmoid setting | IMG_8736 | IMG_8919 | IMG_9029 | IMG_9399 | avg |
|---|---|---|---|---|---|
| contrast 1.65, skew 0.3 | 10.4 | 13.7 | 11.8 | 3.6 | **9.9** |
| contrast 1.65, skew 0.15 | 10.6 | 12.8 | 12.7 | 4.7 | **10.2** |
| contrast 1.8, skew 0.15 | 9.6 | 13.6 | 13.2 | 4.6 | **10.3** |
| contrast 1.5, skew 0.45 | 11.6 | 13.9 | 11.4 | 4.2 | **10.3** |
| contrast 1.8, skew 0.0 | 10.0 | 12.5 | 14.5 | 4.7 | **10.4** |
| contrast 1.65, skew 0.45 | 10.2 | 14.9 | 11.1 | 6.0 | **10.6** |
| contrast 1.8, skew 0.3 | 9.7 | 14.7 | 12.2 | 6.7 | **10.8** |
| contrast 1.5, skew 0.3 | 12.1 | 13.0 | 12.2 | 6.7 | **11.0** |
| contrast 1.65, skew 0.0 | 11.1 | 11.8 | 13.9 | 7.2 | **11.0** |
| contrast 1.95, skew 0.0 | 9.9 | 13.5 | 15.3 | 5.8 | **11.1** |
| contrast 1.5, skew 0.15 | 12.2 | 12.3 | 13.2 | 8.4 | **11.5** |
| contrast 1.95, skew 0.15 | 10.2 | 14.7 | 14.2 | 8.3 | **11.8** |
| contrast 1.8, skew 0.45 | 10.3 | 16.1 | 11.6 | 9.9 | **12.0** |
| contrast 1.5, skew 0.0 | 12.6 | 11.8 | 14.1 | 10.6 | **12.3** |
| preset: scene-referred default | 12.6 | 11.8 | 14.1 | 10.6 | **12.3** |
| preset: ACES 100-nit like | 12.3 | 11.2 | 15.5 | 10.9 | **12.4** |
| contrast 1.95, skew 0.3 | 10.6 | 15.8 | 13.2 | 10.4 | **12.5** |
| contrast 2.1, skew 0.0 | 11.2 | 15.1 | 16.3 | 9.5 | **13.0** |
| preset: smooth | 12.6 | 12.4 | 15.7 | 12.9 | **13.4** |
| contrast 1.95, skew 0.45 | 11.6 | 17.4 | 12.9 | 13.8 | **13.9** |
| contrast 2.1, skew 0.15 | 12.1 | 16.3 | 15.5 | 12.2 | **14.0** |
| contrast 2.1, skew 0.3 | 12.7 | 17.4 | 14.7 | 14.5 | **14.8** |
| preset: neutral gray | 16.5 | 14.2 | 14.8 | 14.0 | **14.9** |
| contrast 2.1, skew 0.45 | 14.0 | 18.9 | 14.5 | 17.9 | **16.3** |
| preset: Reinhard | 21.8 | 19.2 | 23.3 | 29.0 | **23.3** |

Best: **contrast 1.65, skew 0.3** (avg 9.9). Camera JPEG vs the best configuration:

![best vs Camera JPEG](comparison-best.jpg)

## vs Lightroom

| sigmoid setting | IMG_9399 | avg |
|---|---|---|
| contrast 1.8, skew 0.0 | 3.7 | **3.7** |
| contrast 1.65, skew 0.15 | 4.1 | **4.1** |
| contrast 1.65, skew 0.0 | 5.5 | **5.5** |
| contrast 1.95, skew 0.0 | 5.9 | **5.9** |
| contrast 1.5, skew 0.3 | 6.5 | **6.5** |
| contrast 1.5, skew 0.45 | 6.6 | **6.6** |
| contrast 1.65, skew 0.3 | 6.6 | **6.6** |
| contrast 1.8, skew 0.15 | 6.6 | **6.6** |
| contrast 1.5, skew 0.15 | 8.0 | **8.0** |
| contrast 2.1, skew 0.0 | 8.6 | **8.6** |
| contrast 1.95, skew 0.15 | 9.0 | **9.0** |
| preset: ACES 100-nit like | 9.1 | **9.1** |
| contrast 1.8, skew 0.3 | 9.3 | **9.3** |
| contrast 1.65, skew 0.45 | 9.8 | **9.8** |
| contrast 1.5, skew 0.0 | 10.0 | **10.0** |
| preset: scene-referred default | 10.0 | **10.0** |
| contrast 2.1, skew 0.15 | 11.7 | **11.7** |
| contrast 1.95, skew 0.3 | 11.9 | **11.9** |
| preset: smooth | 12.1 | **12.1** |
| contrast 1.8, skew 0.45 | 12.8 | **12.8** |
| contrast 2.1, skew 0.3 | 14.6 | **14.6** |
| preset: neutral gray | 14.7 | **14.7** |
| contrast 1.95, skew 0.45 | 15.5 | **15.5** |
| contrast 2.1, skew 0.45 | 18.2 | **18.2** |
| preset: Reinhard | 29.9 | **29.9** |

Best: **contrast 1.8, skew 0.0** (avg 3.7). Lightroom vs the best configuration:

![best vs Lightroom](comparison-best-lightroom.jpg)
