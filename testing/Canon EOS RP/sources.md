# Test material — Canon EOS RP

RAW+JPEG pairs shot on a Canon EOS RP with Picture Style **Standard**
(the JPEGs are the ground truth the renders are scored against; see the
top-level LICENSE in this folder). The DCP is Adobe's "Camera Standard"
replica of that Picture Style, extracted from the free Adobe DNG Converter
(`ProgramData/Adobe/CameraRaw/CameraProfiles/Camera/Canon EOS RP/`); it is
copyrighted by Adobe and therefore **not** committed — drop it into this
folder to reproduce the results.

sha256 checksums:

```
791560edc94c6957d96fa4e28e2e403b6c26dcb9d03194d8b662c420b54a6780  IMG_8736.CR3
94538dede89723441f4acbf14474beaeecb9627af2a93aad38d426d4c5dbe3b9  IMG_8919.CR3
f793111a050e9ead8ee104a5081720817276c4b818f9cddd7f769e59335eeb08  IMG_9029.CR3
980248133634d534fd15571fe24711beefbe1c1dbb87bfc299ff570e5c41788c  IMG_9399.CR3
0ee672b0411e9323dcc1db8967bd631705d0200cfc96280148a8d5ed5b989faf  IMG_8736.JPG
dba0dcb94c334825a7ec747b3a0ba1396a54eb7ada598be207435f572715c1f4  IMG_8919.JPG
a1d75c7023e2b5963fe03bad54bd7b695e831dc9ee855daa24f46fce60872ade  IMG_9029.JPG
e96e4630ef68c67a7acc9a8ed018bbf6bf4e8e1d47981baedc3308548aca5fc4  IMG_9399.JPG
f1feb64709df243f27dfa29d62e791c170f179119099da0f373a96052296ce55  Canon EOS RP Camera Standard.dcp
```

To regenerate the results (from the repository root, Docker image from
testing/Dockerfile):

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/testing:/work" \
    --entrypoint /env/bin/dcp2icc-suite dcp2icc-testing Canon\ EOS\ RP
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/testing:/work" \
    --entrypoint /env/bin/dcp2icc-sweep dcp2icc-testing Canon\ EOS\ RP
```
