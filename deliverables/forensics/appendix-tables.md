# 附录 A：实测码表（由 build_report.py 从真实 PNG 生成）

每个 RGBA 元组是候选稿在该背景下，96×96 PNG 内点区域 (12, 12, 84, 84) 的唯一像素码（全部内点同码）。

## A.A 家族

| 候选 \ 背景 | bg-midgray | bg-black | bg-white | bg-orange | bg-darkgray | bg-lightgray | bg-red | bg-alpha50 |
|---|---|---|---|---|---|---|---|---|
| A1-opaque | (115, 153, 189, 255) | (115, 153, 189, 255) | (115, 153, 189, 255) | (115, 153, 189, 255) | (115, 153, 189, 255) | (115, 153, 189, 255) | (115, 153, 189, 255) | (115, 153, 189, 255) |
| A2-glaze70 | (115, 153, 189, 255) | (92, 138, 178, 255) | (171, 196, 224, 255) | (164, 150, 178, 255) | (97, 141, 180, 255) | (146, 176, 207, 255) | (146, 139, 178, 255) | (112, 157, 197, 217) |
| A3-glaze50 | (115, 153, 189, 255) | (71, 127, 170, 255) | (198, 219, 243, 255) | (189, 148, 171, 255) | (82, 132, 173, 255) | (163, 190, 218, 255) | (163, 128, 171, 255) | (110, 160, 204, 191) |

判别结论：bg-midgray: 不可分辨；bg-black: 可区分；bg-white: 可区分；bg-orange: 可区分；bg-darkgray: 可区分；bg-lightgray: 可区分；bg-red: 可区分；bg-alpha50: 可区分。

## A.B 家族

| 候选 \ 背景 | bg-midgray | bg-black | bg-white | bg-orange | bg-darkgray | bg-lightgray | bg-red | bg-alpha50 |
|---|---|---|---|---|---|---|---|---|
| B1-mult-full | (96, 96, 96, 255) | (0, 0, 0, 255) | (196, 196, 196, 255) | (186, 86, 17, 255) | (41, 41, 41, 255) | (156, 156, 156, 255) | (156, 17, 17, 255) | (156, 156, 156, 255) |
| B2-mult-glaze | (96, 96, 96, 255) | (0, 0, 0, 255) | (196, 196, 196, 255) | (186, 86, 17, 255) | (41, 41, 41, 255) | (156, 156, 156, 255) | (156, 17, 17, 255) | (134, 134, 134, 223) |
| B3-mult-thin | (96, 96, 96, 255) | (0, 0, 0, 255) | (196, 196, 196, 255) | (186, 86, 17, 255) | (41, 41, 41, 255) | (156, 156, 156, 255) | (156, 17, 17, 255) | (105, 105, 105, 198) |
| B4-brush-alpha | (96, 96, 96, 255) | (0, 0, 0, 255) | (196, 196, 196, 255) | (186, 86, 17, 255) | (41, 41, 41, 255) | (156, 156, 156, 255) | (156, 17, 17, 255) | (134, 134, 134, 223) |

判别结论：bg-midgray: 不可分辨；bg-black: 不可分辨；bg-white: 不可分辨；bg-orange: 不可分辨；bg-darkgray: 不可分辨；bg-lightgray: 不可分辨；bg-red: 不可分辨；bg-alpha50: 可区分。

## A.C 家族

| 候选 \ 背景 | bg-midgray | bg-black | bg-white | bg-orange | bg-darkgray | bg-lightgray | bg-red | bg-alpha50 |
|---|---|---|---|---|---|---|---|---|
| C1-screen-full | (186, 186, 186, 255) | (160, 160, 160, 255) | (255, 255, 255, 255) | (247, 181, 161, 255) | (165, 165, 165, 255) | (224, 224, 224, 255) | (224, 161, 161, 255) | (173, 173, 173, 255) |
| C2-screen-glaze | (186, 186, 186, 255) | (160, 160, 160, 255) | (255, 255, 255, 255) | (247, 181, 161, 255) | (165, 165, 165, 255) | (224, 224, 224, 255) | (224, 161, 161, 255) | (186, 186, 186, 217) |
| C3-screen-thin | (186, 186, 186, 255) | (160, 160, 160, 255) | (255, 255, 255, 255) | (247, 181, 161, 255) | (165, 165, 165, 255) | (224, 224, 224, 255) | (224, 161, 161, 255) | (197, 197, 197, 191) |

判别结论：bg-midgray: 不可分辨；bg-black: 不可分辨；bg-white: 不可分辨；bg-orange: 不可分辨；bg-darkgray: 不可分辨；bg-lightgray: 不可分辨；bg-red: 不可分辨；bg-alpha50: 可区分。

## A.EM 家族

| 候选 \ 背景 | bg-midgray | bg-black | bg-white | bg-orange | bg-darkgray | bg-lightgray | bg-red | bg-alpha50 |
|---|---|---|---|---|---|---|---|---|
| EM1-normal | (71, 87, 107, 255) | (71, 87, 107, 255) | (71, 87, 107, 255) | (71, 87, 107, 255) | (71, 87, 107, 255) | (71, 87, 107, 255) | (71, 87, 107, 255) | (71, 87, 107, 255) |
| EM2-multiply | (71, 87, 107, 255) | (0, 0, 0, 255) | (148, 177, 216, 255) | (141, 78, 20, 255) | (28, 36, 46, 255) | (118, 141, 173, 255) | (118, 14, 20, 255) | (118, 141, 173, 255) |

判别结论：bg-midgray: 不可分辨；bg-black: 可区分；bg-white: 可区分；bg-orange: 可区分；bg-darkgray: 可区分；bg-lightgray: 可区分；bg-red: 可区分；bg-alpha50: 可区分。

## A.ES 家族

| 候选 \ 背景 | bg-midgray | bg-black | bg-white | bg-orange | bg-darkgray | bg-lightgray | bg-red | bg-alpha50 |
|---|---|---|---|---|---|---|---|---|
| ES1-normal | (158, 179, 204, 255) | (158, 179, 204, 255) | (158, 179, 204, 255) | (158, 179, 204, 255) | (158, 179, 204, 255) | (158, 179, 204, 255) | (158, 179, 204, 255) | (158, 179, 204, 255) |
| ES2-screen | (158, 179, 204, 255) | (112, 148, 187, 255) | (255, 255, 255, 255) | (244, 173, 188, 255) | (123, 155, 190, 255) | (213, 221, 231, 255) | (213, 150, 188, 255) | (138, 164, 196, 255) |

判别结论：bg-midgray: 不可分辨；bg-black: 可区分；bg-white: 可区分；bg-orange: 可区分；bg-darkgray: 可区分；bg-lightgray: 可区分；bg-red: 可区分；bg-alpha50: 可区分。

## A.D 家族

| 候选 \ 背景 | bg-midgray | bg-black | bg-white | bg-orange | bg-darkgray | bg-lightgray | bg-red | bg-alpha50 |
|---|---|---|---|---|---|---|---|---|
| D1-zero-opacity | (128, 128, 128, 255) | (0, 0, 0, 255) | (255, 255, 255, 255) | (242, 115, 26, 255) | (56, 56, 56, 255) | (204, 204, 204, 255) | (204, 26, 26, 255) | (128, 128, 128, 128) |
| D2-off-canvas | (128, 128, 128, 255) | (0, 0, 0, 255) | (255, 255, 255, 255) | (242, 115, 26, 255) | (56, 56, 56, 255) | (204, 204, 204, 255) | (204, 26, 26, 255) | (128, 128, 128, 128) |

判别结论：bg-midgray: 不可分辨；bg-black: 不可分辨；bg-white: 不可分辨；bg-orange: 不可分辨；bg-darkgray: 不可分辨；bg-lightgray: 不可分辨；bg-red: 不可分辨；bg-alpha50: 不可分辨。

# 附录 B：量化区间反演

## B.1 家族 A 在中灰单背景下，色刷 sRGB 的可接受区间

观测只给出 8 位码；下表对每个候选假设的覆盖度 o，反演仍能产生该码的全部 sRGB 值。

| 通道 | 观测码 | sRGB 量化区间 | 候选 | o | sRGB 可接受区间 | 声明值 |
|---|---|---|---|---|---|---|
| R | 115 | [0.44902, 0.45294] | A1-opaque | 1.0 | [0.449, 0.4529] | 0.45 |
| R | 115 | [0.44902, 0.45294] | A2-glaze70 | 0.7 | [0.4248, 0.4308] | 0.4263 |
| R | 115 | [0.44902, 0.45294] | A3-glaze50 | 0.5 | [0.3895, 0.3988] | 0.3919 |
| G | 153 | [0.59804, 0.60196] | A1-opaque | 1.0 | [0.598, 0.602] | 0.6 |
| G | 153 | [0.59804, 0.60196] | A2-glaze70 | 0.7 | [0.6343, 0.6395] | 0.6369 |
| G | 153 | [0.59804, 0.60196] | A3-glaze50 | 0.5 | [0.6789, 0.6855] | 0.6822 |
| B | 189 | [0.73922, 0.74314] | A1-opaque | 1.0 | [0.7392, 0.7431] | 0.74 |
| B | 189 | [0.73922, 0.74314] | A2-glaze70 | 0.7 | [0.8158, 0.8207] | 0.8168 |
| B | 189 | [0.73922, 0.74314] | A3-glaze50 | 0.5 | [0.9052, 0.9112] | 0.9064 |

## B.2 半透明背景探针（bg-alpha50）反演有效覆盖度 o

输出直通 alpha 满足 a_out = 0.5 + 0.5·o，因此 o = 2·a_out − 1；
把 alpha 码翻译成 sRGB 区间后得到 o 的闭区间，而不是单点。

| 家族 | 候选 | alpha 码 | o 的量化区间 | 声明 o |
|---|---|---|---|---|
| A | A1-opaque | 255 | [0.9961, 1.0] | 1.0 |
| A | A2-glaze70 | 217 | [0.698, 0.7059] | 0.7 |
| A | A3-glaze50 | 191 | [0.4941, 0.502] | 0.5 |
| B | B1-mult-full | 255 | [0.9961, 1.0] | 1.0 |
| B | B2-mult-glaze | 223 | [0.7451, 0.7529] | 0.75 |
| B | B3-mult-thin | 198 | [0.549, 0.5569] | 0.55 |
| B | B4-brush-alpha | 223 | [0.7451, 0.7529] | 0.75 |
| C | C1-screen-full | 255 | [0.9961, 1.0] | 1.0 |
| C | C2-screen-glaze | 217 | [0.698, 0.7059] | 0.7 |
| C | C3-screen-thin | 191 | [0.4941, 0.502] | 0.5 |

# 附录 C：谱系与可追溯性

共 128 张 PNG，来自 16 个版面，每张图在 render-manifest.json 中记录：版面 id、稿本号、
artifact id、PNG 字节 sha256（与 GET /v1/artifacts 的 ETag 同源）、
scene_digest（稿本摘要）以及对应的源稿 JSON 路径。

| 候选 | 版面 | 背景数 | 源稿样例 |
|---|---|---|---|
| A1-opaque | fp-a1-opaque | 8 | plates/A1-opaque__rev1__bg-midgray.json |
| A2-glaze70 | fp-a2-glaze70 | 8 | plates/A2-glaze70__rev1__bg-midgray.json |
| A3-glaze50 | fp-a3-glaze50 | 8 | plates/A3-glaze50__rev1__bg-midgray.json |
| B1-mult-full | fp-b1-mult-full | 8 | plates/B1-mult-full__rev1__bg-midgray.json |
| B2-mult-glaze | fp-b2-mult-glaze | 8 | plates/B2-mult-glaze__rev1__bg-midgray.json |
| B3-mult-thin | fp-b3-mult-thin | 8 | plates/B3-mult-thin__rev1__bg-midgray.json |
| B4-brush-alpha | fp-b4-brush-alpha | 8 | plates/B4-brush-alpha__rev1__bg-midgray.json |
| C1-screen-full | fp-c1-screen-full | 8 | plates/C1-screen-full__rev1__bg-midgray.json |
| C2-screen-glaze | fp-c2-screen-glaze | 8 | plates/C2-screen-glaze__rev1__bg-midgray.json |
| C3-screen-thin | fp-c3-screen-thin | 8 | plates/C3-screen-thin__rev1__bg-midgray.json |
| D1-zero-opacity | fp-d1-zero-opacity | 8 | plates/D1-zero-opacity__rev1__bg-midgray.json |
| D2-off-canvas | fp-d2-off-canvas | 8 | plates/D2-off-canvas__rev1__bg-midgray.json |
| EM1-normal | fp-em1-normal | 8 | plates/EM1-normal__rev1__bg-midgray.json |
| EM2-multiply | fp-em2-multiply | 8 | plates/EM2-multiply__rev1__bg-midgray.json |
| ES1-normal | fp-es1-normal | 8 | plates/ES1-normal__rev1__bg-midgray.json |
| ES2-screen | fp-es2-screen | 8 | plates/ES2-screen__rev1__bg-midgray.json |

