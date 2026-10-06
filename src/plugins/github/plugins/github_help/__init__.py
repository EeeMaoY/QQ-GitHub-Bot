"""
@Author         :薄荷
@Date           : 2026-10-06 17:58:00
@LastEditors    :薄荷
@LastEditTime   : 2026-10-06 17:58:00
@Description    : GitHub command help
@GitHub         : https://github.com/cscs181/QQ-GitHub-Bot
"""

__author__ = "薄荷"

from nonebot import logger, on_command
from nonebot.adapters import Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from playwright.async_api import Error, TimeoutError
from nonebot.adapters.onebot.v11 import MessageSegment as QQMS
from nonebot.adapters.qq import MessageSegment as QQOfficialMS

from src.plugins.github import config
from src.plugins.github.helpers import NO_GITHUB_EVENT, qqofficial_conditional_image
from src.plugins.github.libs.renderer import help_to_image
from src.plugins.github.libs.renderer.context import (
    HelpCommand,
    HelpContext,
    HelpSection,
)
from src.providers.platform import TARGET_INFO, TargetType

__plugin_meta__ = PluginMetadata(
    "GitHub 指令帮助",
    "查看机器人支持的 GitHub 指令列表",
    "/github help: 查看机器人支持的指令帮助图",
)

HELP_SECTIONS = [
    HelpSection(
        name="仓库绑定",
        commands=[
            HelpCommand(
                name="/bind [owner/repo]",
                description="群绑定 GitHub 仓库，支持绑定多个仓库（仅限群管理员）",
            ),
            HelpCommand(
                name="/unbind [owner/repo]",
                description="群解绑指定仓库，不填写仓库则解绑全部",
            ),
            HelpCommand(
                name="#number",
                description="快捷查看本群默认（首个绑定）仓库的 Issue / PR",
            ),
        ],
    ),
    HelpSection(
        name="事件订阅",
        commands=[
            HelpCommand(name="/subscribe", description="查看当前已有订阅"),
            HelpCommand(
                name="/subscribe owner/repo [event/action ...]",
                description="订阅指定仓库的事件，支持多事件与默认事件列表",
            ),
            HelpCommand(
                name="/unsubscribe owner/repo [event/action ...]",
                description="取消订阅指定仓库的事件",
            ),
        ],
    ),
    HelpSection(
        name="Issue / PR 查看",
        commands=[
            HelpCommand(
                name="owner/repo#number",
                description="快速查看 Issue / PR 信息及事件",
            ),
            HelpCommand(
                name="github.com/owner/repo/...",
                description="通过 Issue / PR / commit 链接快捷查看",
            ),
            HelpCommand(name="/link", description="获取 Issue / PR 链接"),
            HelpCommand(name="/repo [owner/repo]", description="获取仓库链接"),
            HelpCommand(name="/readme [owner/repo]", description="查看仓库 README"),
            HelpCommand(name="/license", description="获取仓库许可证"),
            HelpCommand(
                name="/release [tag]", description="获取仓库最新或指定 Release"
            ),
            HelpCommand(name="/deployment", description="获取仓库 Deployment 列表"),
            HelpCommand(name="/diff", description="查看 PR diff"),
        ],
    ),
    HelpSection(
        name="Issue / PR 操作",
        commands=[
            HelpCommand(name="/comment [message]", description="评论 Issue / PR"),
            HelpCommand(
                name="/label [label ...] / /unlabel [label]",
                description="添加 / 移除标签",
            ),
            HelpCommand(
                name="/close [reason] / /reopen",
                description="关闭 / 重新开启 Issue / PR",
            ),
            HelpCommand(name="/approve [message]", description="批准 PR"),
            HelpCommand(
                name="/merge / /squash / /rebase",
                description="合并 PR",
            ),
            HelpCommand(name="/star / /unstar", description="star / unstar 仓库"),
        ],
    ),
    HelpSection(
        name="账号与其他",
        commands=[
            HelpCommand(
                name="/install [check|revoke]",
                description="安装 GitHub APP 集成",
            ),
            HelpCommand(
                name="/auth [check|revoke]",
                description="授权 APP 以进行用户快捷操作",
            ),
            HelpCommand(
                name="/search [code|repo|user] query",
                description="搜索 GitHub 代码、仓库、用户",
            ),
            HelpCommand(
                name="/contribution [user]", description="获取最近一年的贡献图"
            ),
            HelpCommand(name="/status", description="获取机器人及服务器运行状态"),
            HelpCommand(name="/about", description="获取关于本机器人的信息"),
            HelpCommand(name="/github help", description="查看本指令帮助图"),
        ],
    ),
]


def get_help_context() -> HelpContext:
    """Build the command help context"""
    return HelpContext(
        title="QQ-GitHub-Bot 指令帮助",
        description="在 QQ 内订阅、查看与处理 GitHub Issue / Pull Request",
        sections=HELP_SECTIONS,
    )


github = on_command(
    "github",
    rule=NO_GITHUB_EVENT,
    priority=config.github_command_priority,
    block=True,
)


@github.handle()
async def handle_help(target_info: TARGET_INFO, arg: Message = CommandArg()):
    action = arg.extract_plain_text().strip().lower()
    if action not in {"", "help"}:
        await github.finish(
            "未知子命令，请使用「/github help」查看机器人支持的指令列表"
        )

    try:
        img = await help_to_image(get_help_context())
    except TimeoutError:
        await github.finish("生成图片超时！请稍后再试")
    except Error:
        await github.finish("生成图片出错！请稍后再试")
    except Exception as e:
        logger.opt(exception=e).error(f"Failed while generating help image: {e}")
        await github.finish("生成图片出错！请稍后再试")

    match target_info.type:
        case TargetType.QQ_USER | TargetType.QQ_GROUP:
            await github.send(QQMS.image(img))
        case TargetType.QQ_OFFICIAL_USER | TargetType.QQ_OFFICIAL_GROUP:
            await github.send(await qqofficial_conditional_image(img))
        case TargetType.QQGUILD_USER | TargetType.QQGUILD_CHANNEL:
            await github.send(QQOfficialMS.file_image(img))
