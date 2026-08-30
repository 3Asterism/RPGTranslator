# resources/fonts/ 来源

RPG Maker MV/MZ 注入时用来替换游戏主字体的中文字体，解决日文游戏原版字体不含
简体中文专有字形（比如"么"）导致的缺字问题（Canvas fillText 对 System.json 里
fallbackFonts 多字体链的支持不可靠，实测改这个字段没用，只能直接换主字体本身，
见 engines/mv_mz.py 的 _patch_font 说明）。

## LXGWWenKai-Regular.ttf（霞鹜文楷）

- 来源：https://github.com/lxgw/LxgwWenKai/releases/download/v1.522/LXGWWenKai-Regular.ttf
- 版本：v1.522
- 许可证：SIL Open Font License 1.1（随附 OFL.txt，允许免费打包分发）
- sha256（2026-08-30 下载核对）：
  `39ad71264b588165b469e35e6afb162a378dacd1f95348160240ba9038ac3009`
- 选它的原因：楷体观感比微软雅黑/Noto Sans SC 这类无衬线黑体更贴近视觉小说/
  galgame 对话框常见的字体风格，同时是专门为覆盖尽可能全的简繁中日汉字设计的
  开源字体，在中文字幕/游戏汉化圈子里已经广泛验证过覆盖率。

## OFL.txt

- 来源：https://raw.githubusercontent.com/lxgw/LxgwWenKai/main/OFL.txt
- sha256：`1a25e35da1031c6c3436fde545bb9cb5aca954e9873afe510c834b8b79bd21a0`
- SIL OFL 条款要求随字体一起分发许可证文本，跟字体文件一起打包进注入产物。
