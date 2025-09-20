from datetime import datetime
import json
import secrets
import uuid
from zoneinfo import ZoneInfo

from apscheduler.triggers.date import DateTrigger
from arclet.alconna import Alconna, Args, Subcommand
from nonebot import get_driver
from nonebot.log import logger
from nonebot_plugin_alconna import (
    AlconnaQuery,
    Match,
    Option,
    Query,
    SupportScope,
    Target,
    UniMessage,
    get_bot,
    on_alconna,
)
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_localstore import get_plugin_data_file
from nonebot_plugin_uninfo import Session, UniSession

from src.plugins.lottery.models import Lottery

data_path = get_plugin_data_file("lottery.json")
logger.debug(f"Lottery data path: {data_path}")

lottery_cmd = on_alconna(
    Alconna(
        "lottery",
        Subcommand(
            "n|new",
            Option("number", Args["number", int]),
            Option(
                "start",
                Args["start_time", str],
            ),
            Option("end", Args["end_time", str]),
            Args["keyword?", str],
        ),
        Subcommand(
            "j|join",
            Args["keyword", str],
        ),
        Subcommand(
            "l|list",
        ),
        Subcommand(
            "d|delete",
            Args["keyword", str],
        ),
    ),
    priority=5,
    block=True,
)


async def execute_lottery_and_delete(lottery_id: str, scene_id: str):
    """
    执行彩票抽奖并在抽奖结束后删除彩票。

    :param lottery_id: 彩票的唯一 ID
    :param scene_id: 当前场景的 ID
    """
    # 读取现有的 lottery 数据
    if data_path.exists():
        with data_path.open("r", encoding="utf-8") as file:
            existing_data = json.load(file)
    else:
        existing_data = {}

    if scene_id not in existing_data:
        logger.warning(f"No lotteries found in scene {scene_id}.")
        return

    # Convert all lottery data to Lottery instances
    lottery_data: list[Lottery] = [
        Lottery.model_validate(lottery)  # Convert each dictionary to a Lottery instance
        for lottery in existing_data[scene_id]
    ]

    lottery_to_draw = next(
        (lottery for lottery in lottery_data if lottery.id == lottery_id), None
    )

    if not lottery_to_draw:
        logger.warning(f"No lottery found with ID {lottery_id}.")
        return

    if not lottery_to_draw.participants:
        logger.info(f"Lottery {lottery_to_draw.keyword} has no participants to draw.")
        return

    winner = secrets.choice(lottery_to_draw.participants)

    logger.info(f"Lottery {lottery_to_draw.keyword} has ended. Winner is {winner}.")

    # Remove the lottery from existing data
    existing_data[scene_id] = [
        lottery.model_dump() for lottery in lottery_data if lottery.id != lottery_id
    ]

    with data_path.open("w", encoding="utf-8") as file:
        json.dump(existing_data, file, ensure_ascii=False, indent=4)

    logger.debug(f"Lottery {lottery_to_draw.keyword} has been deleted from data.")
    await UniMessage(f"Lottery {lottery_to_draw.keyword} has ended. ").send(
            target=Target.group(f"{scene_id}", SupportScope.qq_client),
            bot=await get_bot(
                bot_id=lottery_to_draw.bot_id, adapter=lottery_to_draw.adapter
            ),
        )
    await (
        UniMessage.template(
            "Winner is {:At(user, target)}."
        )
        .format(target=winner)
        .send(
            target=Target.group(f"{scene_id}", SupportScope.qq_client),
            bot=await get_bot(
                bot_id=lottery_to_draw.bot_id, adapter=lottery_to_draw.adapter
            ),
        )
    )



def schedule_lottery_task(lottery_id: str, scene_id: str, end_time: datetime):
    """
    根据传入的end_time为彩票创建一个定时任务，任务将在指定时间触发。

    :param lottery_id: 彩票的唯一 ID
    :param scene_id: 当前场景的 ID
    :param end_time: 抽奖结束时间，格式：YYYY-MM-DD HH:MM:SS
    """
    scheduler.add_job(
        execute_lottery_and_delete,
        DateTrigger(run_date=end_time),
        args=[lottery_id, scene_id],
        id=lottery_id,
    )

    logger.info(f"Task for lottery {lottery_id} scheduled at {end_time}.")


@lottery_cmd.assign("new")
async def handle_new(
    keyword: Match[str],
    session: Session = UniSession(),
    number: Query[int] = AlconnaQuery("number", 1),
    start_time: Query[str] = AlconnaQuery(
        "start_time",
        datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d/%H:%M:%S"),
    ),
    end_time: Query[str] = AlconnaQuery(
        "end_time",
        (
            datetime.now(ZoneInfo("Asia/Shanghai")).replace(
                hour=23, minute=59, second=59
            )
        ).strftime("%Y-%m-%d/%H:%M:%S"),
    ),
):
    logger.debug(
        f"Trying to create a new lottery from {session.scene.id} by {session.user.id}"
    )

    if (
        session.member
        and session.member.id != 1
        and session.user.id not in get_driver().config.superusers
    ):
        await lottery_cmd.finish("Only superusers can create a lottery")

    if not keyword.available:
        await lottery_cmd.finish("No keyword provided")

    _keyword = keyword.result

    try:
        _start_time = datetime.strptime(start_time.result, "%Y-%m-%d/%H:%M:%S").replace(
            tzinfo=ZoneInfo("Asia/Shanghai")
        )
        _end_time = datetime.strptime(end_time.result, "%Y-%m-%d/%H:%M:%S").replace(
            tzinfo=ZoneInfo("Asia/Shanghai")
        )
        if _start_time < datetime.now(ZoneInfo("Asia/Shanghai")):
            await lottery_cmd.finish("Start time must be in the future")

        if _start_time >= _end_time:
            await lottery_cmd.finish("End time must be after start time")
    except ValueError:
        await lottery_cmd.finish("End time format error, use YYYY-MM-DD HH:MM:SS")

    # Check if a lottery with the same keyword already exists in the scene
    if data_path.exists():
        with data_path.open("r", encoding="utf-8") as file:
            existing_data = json.load(file)
    else:
        existing_data = {}

    scene_id = str(session.scene.id)

    if scene_id in existing_data:
        for lottery in existing_data[scene_id]:
            if lottery.get("keyword") == _keyword:
                await lottery_cmd.finish(
                    f"A lottery with the keyword '{_keyword}' already exists in this scene."
                )

    lottery = Lottery(
        id=str(uuid.uuid4()),
        creator=session.user.id,
        scene=session.scene.id,
        keyword=_keyword,
        participants_limits=number.result,
        start_time=_start_time.strftime("%Y-%m-%d %H:%M:%S"),
        end_time=_end_time.strftime("%Y-%m-%d %H:%M:%S"),
        bot_id=session.self_id,
        adapter=session.adapter,
    )

    if scene_id not in existing_data:
        existing_data[scene_id] = []

    existing_data[scene_id].append(lottery.model_dump())

    with data_path.open("w", encoding="utf-8") as file:
        json.dump(existing_data, file, ensure_ascii=False, indent=4)

    schedule_lottery_task(lottery.id, scene_id, _end_time)

    title = f"Lottery: {_keyword}" if keyword.available else "Lottery"
    await lottery_cmd.finish(
        f"{title} created with start time [{lottery.start_time}], end time"
        f" [{lottery.end_time}], maximum {number.result} participants "
        f"by {session.user.id}"
    )


@lottery_cmd.assign("join")
async def handle_join(
    keyword: Match[str],
    session: Session = UniSession(),
):
    if data_path.exists():
        with data_path.open("r", encoding="utf-8") as file:
            existing_data = json.load(file)
    else:
        existing_data = {}

    scene_id = str(session.scene.id)
    if scene_id not in existing_data:
        await lottery_cmd.finish("No lottery found in this scene")

    # Convert each dictionary to a Lottery instance
    lottery_data = [
        Lottery.model_validate(lottery)  # Convert each dictionary to a Lottery instance
        for lottery in existing_data[scene_id]
    ]

    _keyword = keyword.result if keyword.available else ""
    matching_lotteries = [
        lottery for lottery in lottery_data if _keyword in lottery.keyword
    ]

    if matching_lotteries:
        for lottery in matching_lotteries:
            # Check if the user is already a participant
            if session.user.id in lottery.participants:
                await lottery_cmd.finish(f"You have already joined the lottery '{_keyword}'.")

            # Add the user to the participants list
            lottery.participants.append(session.user.id)

        # Update the lottery data
        with data_path.open("w", encoding="utf-8") as file:
            existing_data[scene_id] = [lottery.model_dump() for lottery in lottery_data]
            json.dump(existing_data, file, ensure_ascii=False, indent=4)

        await lottery_cmd.finish(f"Joined lottery {_keyword} successfully")
    else:
        await lottery_cmd.finish(
            f"No lotteries found matching the keyword '{_keyword}'"
        )



@lottery_cmd.assign("list")
async def handle_list(session: Session = UniSession()):
    # 读取现有的 lottery 数据
    if data_path.exists():
        with data_path.open("r", encoding="utf-8") as file:
            existing_data = json.load(file)
    else:
        existing_data = {}

    scene_id = str(session.scene.id)

    if scene_id not in existing_data or not existing_data[scene_id]:
        await lottery_cmd.finish("No lottery found in this scene")

    # Convert all lottery data to Lottery instances
    lottery_data = [
        Lottery.model_validate(lottery)  # Convert each dictionary to a Lottery instance
        for lottery in existing_data[scene_id]
    ]

    if not lottery_data:
        await lottery_cmd.finish("No lottery found in this scene")

    lottery_list = [
        f"Keyword: {lottery.keyword}, Start: {lottery.start_time}, End: {lottery.end_time}, "
        f"Participants: {len(lottery.participants)} / {lottery.participants_limits}"
        for lottery in lottery_data
    ]

    await lottery_cmd.finish("\n".join(lottery_list))



@lottery_cmd.assign("delete")
async def handle_delete(
    keyword: Match[str],
    session: Session = UniSession(),
):
    if data_path.exists():
        with data_path.open("r", encoding="utf-8") as file:
            existing_data = json.load(file)
    else:
        existing_data = {}

    scene_id = str(session.scene.id)

    if scene_id not in existing_data or not existing_data[scene_id]:
        await lottery_cmd.finish("No lottery found in this scene")

    lottery_data: list[Lottery] = existing_data[scene_id]

    _keyword = keyword.result if keyword.available else ""

    matching_lotteries = [
        lottery for lottery in lottery_data if _keyword in lottery.keyword
    ]

    if matching_lotteries:
        for lottery in matching_lotteries:
            if (
                session.user.id != lottery.creator
                and session.user.id not in get_driver().config.superusers
            ):
                await lottery_cmd.finish(
                    "You are not authorized to delete this lottery"
                )

            try:
                scheduler.remove_job(lottery.id)
                logger.info(
                    f"Job for lottery {lottery.keyword} with ID {lottery.id} has been "
                    f"removed."
                )
            except Exception as e:
                logger.error(f"Failed to remove job for lottery {lottery.keyword}: {e}")

            existing_data[scene_id].remove(lottery.model_dump())

        with data_path.open("w", encoding="utf-8") as file:
            json.dump(existing_data, file, ensure_ascii=False, indent=4)

        await lottery_cmd.finish(
            f"Lottery with keyword '{_keyword}' deleted successfully"
        )
    else:
        await lottery_cmd.finish(
            f"No lotteries found matching the keyword '{_keyword}'"
        )
