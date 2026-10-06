"""
@Author         : yanyongyu
@Date           : 2021-03-12 15:03:23
@LastEditors    : yanyongyu
@LastEditTime   : 2023-12-11 13:42:18
@Description    : None
@GitHub         : https://github.com/yanyongyu
"""

__author__ = "yanyongyu"

import re

from nonebot.typing import T_State
from nonebot.matcher import Matcher
from nonebot.adapters import Message
from nonebot import logger, on_command
from nonebot.plugin import PluginMetadata
from nonebot.params import CommandArg, ArgPlainText

from src.plugins.github import config
from src.plugins.github.models import Group
from src.providers.platform import GROUP_INFO
from src.plugins.github.libs.github import FULLREPO_REGEX
from src.plugins.github.helpers import (
    GROUP_SUPERPERM,
    NO_GITHUB_EVENT,
    MATCH_WHEN_GROUP,
)
from src.plugins.github.dependencies import (
    BINDED_GROUP,
    GITHUB_REPO_INSTALLATION,
    allow_cancellation,
)

__plugin_meta__ = PluginMetadata(
    "GitHub 群仓库绑定",
    "群绑定 GitHub 仓库以进行快捷 Issue、PR 相关操作（仅限群管理员）",
    "/bind [owner/repo]: 群查询或绑定 GitHub 仓库，可绑定多个仓库"
    "（仅仓库安装 APP 后有效）\n"
    "/unbind [owner/repo]: 群解绑指定仓库，不填写仓库则解绑全部\n"
    "/repos: 查看本群已绑定的仓库列表",
)

bind = on_command(
    "bind",
    aliases={"绑定"},
    rule=MATCH_WHEN_GROUP & NO_GITHUB_EVENT,
    permission=GROUP_SUPERPERM,
    priority=config.github_command_priority,
    block=True,
)


@bind.handle()
async def process_arg(matcher: Matcher, arg: Message = CommandArg()):
    if full_name := arg.extract_plain_text().strip():
        matcher.set_arg("full_name", arg.__class__(full_name))


@bind.got(
    "full_name",
    prompt="请发送要绑定的仓库全名，例如：「owner/repo」",
    parameterless=(allow_cancellation("已取消"),),
)
async def process_repo(state: T_State, full_name: str = ArgPlainText()):
    if not (matched := re.match(f"^{FULLREPO_REGEX}$", full_name)):
        await bind.reject(
            f"仓库名 {full_name} 错误！\n请重新发送正确的仓库名，"
            "例如：「owner/repo」\n或发送「取消」以取消"
        )

    state["owner"] = matched["owner"]
    state["repo"] = matched["repo"]


@bind.handle()
async def handle_bind(
    state: T_State, group_info: GROUP_INFO, repo_installation: GITHUB_REPO_INSTALLATION
):
    owner = state["owner"]
    repo = state["repo"]
    full_name = f"{owner}/{repo}"

    try:
        group = await Group.from_info(group_info)
    except Exception as e:
        logger.opt(exception=e).error(f"Failed while getting group: {e}")
        await bind.finish("未知错误发生，请尝试重试或联系管理员")

    repos = group.bind_repos if group else []
    if full_name in repos:
        await bind.finish(
            f"本群已绑定仓库 {full_name} ！\n当前已绑定：{'、'.join(repos)}"
        )

    repos.append(full_name)
    try:
        await Group.create_or_update_by_info(group_info, bind_repo=",".join(repos))
    except Exception as e:
        logger.opt(exception=e).error(f"Failed while binding group: {e}")
        await bind.finish("未知错误发生，请尝试重试或联系管理员")

    await bind.finish(
        f"本群成功绑定仓库 {full_name} ！\n当前已绑定：{'、'.join(repos)}"
    )


unbind = on_command(
    "unbind",
    aliases={"解绑"},
    rule=MATCH_WHEN_GROUP & NO_GITHUB_EVENT,
    permission=GROUP_SUPERPERM,
    priority=config.github_command_priority,
    block=True,
)


@unbind.handle()
async def process_unbind(group: BINDED_GROUP, arg: Message = CommandArg()):
    full_name = arg.extract_plain_text().strip()
    if full_name and full_name not in group.bind_repos:
        await unbind.finish(f"本群未绑定仓库 {full_name} ！")

    try:
        if full_name:
            await group.remove_repo(full_name)
        else:
            await group.unbind()
    except Exception as e:
        logger.opt(exception=e).error(f"Failed while unbind group: {e}")
        await unbind.finish("未知错误发生，请尝试重试或联系管理员")

    if not full_name:
        await unbind.finish("成功解绑全部仓库！")

    if remaining := group.bind_repos:
        await unbind.finish(
            f"成功解绑仓库 {full_name} ！\n当前已绑定：{'、'.join(remaining)}"
        )
    await unbind.finish(f"成功解绑仓库 {full_name} ！\n当前已无绑定仓库")


repos = on_command(
    "repos",
    aliases={"仓库列表", "绑定列表"},
    rule=MATCH_WHEN_GROUP & NO_GITHUB_EVENT,
    priority=config.github_command_priority,
    block=True,
)


@repos.handle()
async def handle_repos(group_info: GROUP_INFO):
    try:
        group = await Group.from_info(group_info)
    except Exception as e:
        logger.opt(exception=e).error(f"Failed while getting group: {e}")
        await repos.finish("未知错误发生，请尝试重试或联系管理员")

    bound_repos = group.bind_repos if group else []
    if not bound_repos:
        await repos.finish("本群暂无绑定仓库")

    if len(bound_repos) == 1:
        await repos.finish(f"本群已绑定仓库：{bound_repos[0]}")

    # use the first bound repo as the default for #number when multiple repos
    lines = [
        f"{repo}（默认）" if index == 0 else repo
        for index, repo in enumerate(bound_repos)
    ]
    await repos.finish(
        "本群已绑定仓库（默认仓库为 #number 快捷查看的目标）：\n" + "\n".join(lines)
    )
