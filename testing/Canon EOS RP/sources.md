# Test material — Canon EOS RP

RAW+JPEG pairs shot on a Canon EOS RP (Picture Style **Standard**, except
`19-43-22-103` which used **Auto**), plus full-size Lightroom exports of
some raws (`lightroom_*.jpg`) as additional sources of truth. The JPEGs
are the ground truth the renders are scored against; see the LICENSE in
this folder. The DCP is Adobe's "Camera Standard" replica of that Picture
Style, extracted from the free Adobe DNG Converter
(`ProgramData/Adobe/CameraRaw/CameraProfiles/Camera/Canon EOS RP/`); it is
copyrighted by Adobe and therefore **not** committed. To reproduce the
results, run `camicc-fetch-dcps` once from the repository root (it
downloads Adobe DNG Converter and extracts every camera profile into
`dcps/`); the test tools then auto-match the right profile from each
JPEG's camera model and Picture Style.

sha256 checksums:

```
bb95a0274144986d2d7adfccb8e5e308532e2d0539c2ccdcc155393889782d26  19-43-22-103.CR3
791560edc94c6957d96fa4e28e2e403b6c26dcb9d03194d8b662c420b54a6780  IMG_8736.CR3
94538dede89723441f4acbf14474beaeecb9627af2a93aad38d426d4c5dbe3b9  IMG_8919.CR3
f793111a050e9ead8ee104a5081720817276c4b818f9cddd7f769e59335eeb08  IMG_9029.CR3
980248133634d534fd15571fe24711beefbe1c1dbb87bfc299ff570e5c41788c  IMG_9399.CR3
5f8a86693a51d80384653d1af41ea7926213acd557d762a181d315f7ab8dac4a  19-43-22-103.JPG
0ee672b0411e9323dcc1db8967bd631705d0200cfc96280148a8d5ed5b989faf  IMG_8736.JPG
dba0dcb94c334825a7ec747b3a0ba1396a54eb7ada598be207435f572715c1f4  IMG_8919.JPG
a1d75c7023e2b5963fe03bad54bd7b695e831dc9ee855daa24f46fce60872ade  IMG_9029.JPG
e96e4630ef68c67a7acc9a8ed018bbf6bf4e8e1d47981baedc3308548aca5fc4  IMG_9399.JPG
8343654edf6c2d235f6b49d219ffb2467338d16d6118a40559420f5f30047994  lightroom_19-43-22-103.jpg
e1e2c1e1e8fc737357bf64716195a2796fa7f6fd8fa190c2e7a03aff0be82971  lightroom_IMG_9399.jpg
f1feb64709df243f27dfa29d62e791c170f179119099da0f373a96052296ce55  Canon EOS RP Camera Standard.dcp
```

To regenerate the results (from the repository root, Docker image from
testing/Dockerfile):

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/testing:/work" \
    --entrypoint /env/bin/camicc-suite camicc-testing Canon\ EOS\ RP
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/testing:/work" \
    --entrypoint /env/bin/camicc-sweep camicc-testing Canon\ EOS\ RP
```
