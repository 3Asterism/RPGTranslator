"""VX Ace 消息框运行时像素级换行补丁（spec 9.2.b）。

设计依据：从真实 WOLF RPG Editor 项目转向验证 VX Ace 时，用一个真实
VX Ace 工程（RTP + `Data/*.rvdata2` + `Game.exe` + `System/RGSS301.dll`
齐全，可以直接跑）反编译出的 `Window_Base`/`Window_Message` 真实脚本源码
确认了两件事，而不是凭记忆猜 RGSS3 内部实现：

1. `Window_Base#process_normal_character` 本来就是逐字符用
   `contents.text_size(c).width` 量出真实像素宽度再画（`contents` 是当前
   窗口实际用的字体/`Bitmap`），只是从不检查会不会画出窗口右边界——这正是
   本补丁要打的点：在画之前多一步"画了会不会溢出"的判断。
2. 4 行溢出后的"等待确认 -> 清屏 -> 续显"分页机制 `Window_Message` 也已经
   自带（`process_new_line` 里的 `need_new_page?` + `input_pause` +
   `new_page`），只是只在源文本本来就带 `\n` 换行符时才会触发。本补丁复用
   这条已有分页逻辑，不用自己重新实现一遍——补丁只需要在探测到会画出边界
   的时候，主动调用一次 `process_new_line`，让它以为"这里本来就有个换行"，
   分页判断自然照旧生效。

补丁不改动 Python 侧已有的 `rewrap_paragraph`（按估算宽度重新分行、塞回原
4 个 `Show Text` 槽位）——两者是叠加关系：估算重排先把译文按原有分页节奏
摊开，运行时补丁再对每一行做一次真实像素宽度校正；估算严重偏差、单槽位文本
过长时，运行时补丁会连续触发多次自动分页，行为和用户在 spec 里选定的
"超过 4 行自动分页" 方案一致，不需要额外实现。
"""

from __future__ import annotations

RUNTIME_LINE_WRAP_SCRIPT_NAME = "RPGTranslator_RuntimeLineWrap"

RUNTIME_LINE_WRAP_SOURCE = """\
# RPGTranslator 自动生成：消息框运行时像素级动态换行补丁。
# 只在检测不到已知第三方消息系统脚本时才会被注入（见工具侧
# has_conflicting_message_system），避免和自定义 Window_Message 打架。
class Window_Message < Window_Base
  alias rpgtranslator_orig_process_character process_character

  def process_character(c, text, pos)
    if c != "\\n" && c != "\\f" && c != "\\e" && c != ""
      text_width = text_size(c).width
      if pos[:x] + text_width > contents.width
        process_new_line(text, pos)
      end
    end
    rpgtranslator_orig_process_character(c, text, pos)
  end
end
"""
