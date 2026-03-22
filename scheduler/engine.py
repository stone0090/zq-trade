"""定时任务引擎 — APScheduler 封装"""
import uuid
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

from web.database import get_db

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None

# ─── 任务定义 ───

JOB_DEFINITIONS = {
    "daily_scan": {
        "name": "在库品种扫描",
        "trigger": "cron",
        "trigger_args": {"day_of_week": "sat", "hour": 8, "minute": 0},
        "func": "scheduler.jobs:daily_scan",
        "enabled": True,
    },
    "focus_monitor": {
        "name": "重点关注监控",
        "trigger": "interval",
        "trigger_args": {"minutes": 5},
        "func": "scheduler.jobs:focus_monitor",
        "enabled": True,
    },
    "watch_monitor": {
        "name": "关注中监控",
        "trigger": "interval",
        "trigger_args": {"minutes": 60},
        "func": "scheduler.jobs:watch_monitor",
        "enabled": True,
    },
    "news_collect": {
        "name": "新闻异动采集",
        "trigger": "interval",
        "trigger_args": {"minutes": 30},
        "func": "scheduler.jobs:news_collect",
        "enabled": True,
    },
    "daily_report": {
        "name": "收盘日报推送",
        "trigger": "cron",
        "trigger_args": {"hour": 16, "minute": 30},
        "func": "scheduler.jobs:daily_report",
        "enabled": True,
    },
}


def _log_job_event(event):
    """记录任务执行结果到 job_logs"""
    job_id = event.job_id
    now = datetime.now().isoformat()
    duration_ms = int((event.scheduled_run_time.timestamp() - datetime.now().timestamp()) * -1000) if event.scheduled_run_time else 0

    try:
        with get_db() as conn:
            if event.exception:
                conn.execute(
                    """INSERT INTO job_logs (id, job_name, started_at, finished_at, duration_ms, status, error_message)
                       VALUES (?, ?, ?, ?, ?, 'error', ?)""",
                    (str(uuid.uuid4()), job_id, now, now, max(0, duration_ms), str(event.exception))
                )
            else:
                result = str(event.retval) if event.retval else ""
                conn.execute(
                    """INSERT INTO job_logs (id, job_name, started_at, finished_at, duration_ms, status, result_summary)
                       VALUES (?, ?, ?, ?, ?, 'success', ?)""",
                    (str(uuid.uuid4()), job_id, now, now, max(0, duration_ms), result[:500])
                )
    except Exception as e:
        logger.warning(f"记录任务日志失败: {e}")


def _restore_pause_states():
    """服务启动后，从 job_config.paused 恢复暂停状态"""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT job_id FROM job_config WHERE paused = 1"
            ).fetchall()
        for r in rows:
            job_id = r['job_id']
            if _scheduler and _scheduler.get_job(job_id):
                _scheduler.pause_job(job_id)
                logger.info(f"恢复暂停状态: {job_id}")
    except Exception as e:
        logger.warning(f"恢复暂停状态失败: {e}")


def start_scheduler():
    """启动定时任务调度器"""
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _scheduler.add_listener(_log_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    for job_id, job_def in JOB_DEFINITIONS.items():
        if not job_def["enabled"]:
            continue

        if job_def["trigger"] == "cron":
            trigger = CronTrigger(**job_def["trigger_args"])
        else:
            trigger = IntervalTrigger(**job_def["trigger_args"])

        _scheduler.add_job(
            job_def["func"],
            trigger=trigger,
            id=job_id,
            name=job_def["name"],
            replace_existing=True,
            misfire_grace_time=60,
        )

    _scheduler.start()

    # 恢复持久化的暂停状态
    _restore_pause_states()

    logger.info(f"定时任务引擎启动，{sum(1 for d in JOB_DEFINITIONS.values() if d['enabled'])} 个任务已注册")


def stop_scheduler():
    """停止定时任务调度器"""
    global _scheduler
    if _scheduler and _scheduler.running:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("定时任务引擎已停止")
        except Exception as e:
            logger.warning(f"定时任务引擎停止时出现异常(可忽略): {e}")


def get_scheduler() -> Optional[BackgroundScheduler]:
    return _scheduler


# ─── 默认任务描述 ───

_DEFAULT_DESCRIPTIONS = {
    "daily_scan": "每周六08:00扫描「在库中」(idle)品种池：\n1. 先增量获取每只股票最新价格和K线数据\n2. 获取六维有效评级（系统评级优先，无则取人工标注）\n3. 满足关注条件（DL=S, PT≥B, LK≥B, SF≤2nd）→ 升级到「关注中」\n4. 完全无数据品种（无名称+无评级+无价格）→ 自动移除\n5. 有变动时发送通知",
    "focus_monitor": "每5分钟检查「重点关注」(focused)品种：\n1. 先增量获取每只股票最新价格和K线数据\n2. 六维全部达标（DL=S, PT≥A, LK≥A, SF=1st, TY≥A, DN≥A）→ 执行模拟下单并转入「持仓中」\n3. 不满足重点条件（DL=S, PT≥A, LK≥A, SF=1st, TY≥A）但仍满足关注条件 → 降级回「关注中」\n4. 连关注条件都不满足 → 降级回「在库中」\n5. 触发下单或降级时发送通知",
    "watch_monitor": "每1小时检查「关注中」(watching)品种：\n1. 先增量获取每只股票最新价格和K线数据\n2. 满足重点条件（DL=S, PT≥A, LK≥A, SF=1st, TY≥A）→ 升级到「重点关注」\n3. 不满足关注条件（DL=S, PT≥B, LK≥B, SF≤2nd）→ 降级回「在库中」\n4. 不会自动移除品种，仅做升降级\n5. 升降级时发送通知",
    "news_collect": "每30分钟采集监控品种相关新闻：\n1. 获取「关注中」「重点关注」「持仓中」三个状态的品种\n2. 通过Yahoo Finance获取每只品种最新5条新闻\n3. 关键词异动检测（earnings/acquisition/merger/财报/并购/回购等）\n4. 新增新闻保存到数据库（自动去重）\n5. 检测到异动新闻时标记news_alert并发送通知",
    "daily_report": "每天16:30收盘后推送日报：\n1. 汇总「重点关注」「关注中」「持仓中」品种数量\n2. 统计累计交易数、胜率、总盈亏、最大回撤\n3. 列出每只持仓的浮动盈亏金额和百分比\n4. 通过配置的通知渠道（飞书/企微等）推送卡片消息",
}


def _load_job_configs() -> dict:
    """从 job_config 表加载自定义配置"""
    configs = {}
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM job_config").fetchall()
            for r in rows:
                configs[r['job_id']] = dict(r)
    except Exception:
        pass
    return configs


def get_all_jobs_status() -> list[dict]:
    """获取所有任务状态
    state: active=调度中, paused=已暂停, disabled=未启用/调度器未运行
    """
    configs = _load_job_configs()

    result = []
    for job_id, job_def in JOB_DEFINITIONS.items():
        cfg = configs.get(job_id, {})
        default_desc = _DEFAULT_DESCRIPTIONS.get(job_id, "")

        info = {
            "id": job_id,
            "name": cfg.get("custom_name") or job_def["name"],
            "description": cfg.get("custom_description") or default_desc,
            "trigger": job_def["trigger"],
            "trigger_args": job_def["trigger_args"],
            "enabled": job_def["enabled"],
            "state": "disabled",
            "next_run": None,
            "sort_order": cfg.get("sort_order", 999),
        }

        if _scheduler and _scheduler.running:
            job = _scheduler.get_job(job_id)
            if job:
                if job.next_run_time is not None:
                    info["state"] = "active"
                    info["next_run"] = job.next_run_time.isoformat()
                else:
                    info["state"] = "paused"
            else:
                info["state"] = "disabled"
        else:
            info["state"] = "disabled"

        result.append(info)

    result.sort(key=lambda x: x["sort_order"])
    return result


def pause_job(job_id: str) -> bool:
    if _scheduler and _scheduler.get_job(job_id):
        _scheduler.pause_job(job_id)
        _persist_pause_state(job_id, True)
        logger.info(f"任务 {job_id} 已暂停")
        return True
    return False


def resume_job(job_id: str) -> bool:
    if _scheduler and _scheduler.get_job(job_id):
        _scheduler.resume_job(job_id)
        _persist_pause_state(job_id, False)
        logger.info(f"任务 {job_id} 已恢复")
        return True
    return False


def _persist_pause_state(job_id: str, paused: bool):
    """将暂停状态持久化到 job_config 表"""
    try:
        with get_db() as conn:
            existing = conn.execute(
                "SELECT job_id FROM job_config WHERE job_id=?", (job_id,)
            ).fetchone()
            if existing:
                conn.execute("UPDATE job_config SET paused=? WHERE job_id=?", (1 if paused else 0, job_id))
            else:
                conn.execute(
                    "INSERT INTO job_config (job_id, paused, sort_order) VALUES (?,?,?)",
                    (job_id, 1 if paused else 0, 999)
                )
    except Exception as e:
        logger.warning(f"持久化暂停状态失败: {e}")


def run_job_now(job_id: str) -> bool:
    """立即执行一次任务"""
    job_def = JOB_DEFINITIONS.get(job_id)
    if not job_def:
        return False
    if _scheduler and _scheduler.running:
        _scheduler.add_job(
            job_def["func"],
            id=f"{job_id}_manual_{uuid.uuid4().hex[:8]}",
            name=f"{job_def['name']}(手动)",
            replace_existing=False,
        )
        return True
    return False


def update_job_trigger(job_id: str, trigger_type: str, trigger_args: dict) -> bool:
    """更新任务的调度周期"""
    if job_id not in JOB_DEFINITIONS:
        return False

    # 更新定义
    JOB_DEFINITIONS[job_id]["trigger"] = trigger_type
    JOB_DEFINITIONS[job_id]["trigger_args"] = trigger_args

    # 如果调度器运行中，重新调度任务
    if _scheduler and _scheduler.running:
        if trigger_type == "cron":
            trigger = CronTrigger(**trigger_args)
        else:
            trigger = IntervalTrigger(**trigger_args)

        _scheduler.reschedule_job(job_id, trigger=trigger)
        logger.info(f"已更新任务 {job_id} 的调度周期: {trigger_type} {trigger_args}")

    return True


def update_job_config(job_id: str, name: str = None, description: str = None) -> bool:
    """更新任务自定义名称和描述"""
    if job_id not in JOB_DEFINITIONS:
        return False
    try:
        with get_db() as conn:
            existing = conn.execute(
                "SELECT job_id FROM job_config WHERE job_id=?", (job_id,)
            ).fetchone()
            if existing:
                if name is not None:
                    conn.execute("UPDATE job_config SET custom_name=? WHERE job_id=?", (name, job_id))
                if description is not None:
                    conn.execute("UPDATE job_config SET custom_description=? WHERE job_id=?", (description, job_id))
            else:
                conn.execute(
                    "INSERT INTO job_config (job_id, custom_name, custom_description, sort_order) VALUES (?,?,?,?)",
                    (job_id, name, description, 0)
                )
        return True
    except Exception as e:
        logger.error(f"更新任务配置失败: {e}")
        return False


def update_jobs_order(job_ids: list) -> bool:
    """根据传入的 job_id 列表更新排序"""
    try:
        with get_db() as conn:
            for idx, job_id in enumerate(job_ids):
                existing = conn.execute(
                    "SELECT job_id FROM job_config WHERE job_id=?", (job_id,)
                ).fetchone()
                if existing:
                    conn.execute("UPDATE job_config SET sort_order=? WHERE job_id=?", (idx, job_id))
                else:
                    conn.execute(
                        "INSERT INTO job_config (job_id, sort_order) VALUES (?,?)",
                        (job_id, idx)
                    )
        return True
    except Exception as e:
        logger.error(f"更新任务排序失败: {e}")
        return False
