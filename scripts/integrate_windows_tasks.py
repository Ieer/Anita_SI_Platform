# 功能：从 Windows 任务计划程序获取任务并自动整合到项目的任务数据库
# python scripts/integrate_windows_tasks.py

"""
支持的触发器类型：
每周任务 → cron 触发器
每日任务 → cron 触发器
登录/启动时 → date 触发器
其他 → interval 触发器（默认3600秒）
"""
# Add paths
import os
import sys

# 打印当前文件路径，调试用
print(f"当前文件路径: {os.path.abspath(__file__)}")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from datetime import datetime  # noqa: E402
from dash_components.windows_tasks_manager import WindowsTasksManager  # noqa: E402
from common.utilities.util_apscheduler import add_local_cron_job, add_local_interval_job, add_local_date_job  # noqa: E402
from common.utilities.util_logger import Log  # noqa: E402

logger = Log.get_logger(__name__)

def parse_trigger(triggers_str):
    """解析 Windows 任务的 Triggers 字段，返回项目兼容的触发器信息"""
    # 简化解析逻辑，根据 windows_scheduler.md 的规则
    if "every" in triggers_str and "week" in triggers_str:
        # Weekly -> cron
        # 示例：提取时间和星期几，构建 cron 表达式
        # 这里需要更复杂的正则解析，简化示例
        cron_list = ['*', '15', '*', '*', '*', '0,1,6', '*', '*']  # 每周日、一、六下午 3:35
        return {"type": "cron", "cron_list": cron_list}
    elif "every day" in triggers_str:
        # Daily -> cron
        cron_list = ['*', '15', '*', '*', '*', '*', '*', '*']
        return {"type": "cron", "cron_list": cron_list}
    elif "At log on" in triggers_str or "At startup" in triggers_str:
        # Date -> date
        return {"type": "date", "run_date": datetime.now().isoformat()}
    else:
        # 默认 interval 或跳过
        return {"type": "interval", "seconds": 3600}

def integrate_windows_tasks():
    """从 Windows 任务计划程序获取任务并整合到项目数据库"""
    manager = WindowsTasksManager()
    tasks = manager.get_scheduled_tasks()

    if not tasks:
        logger.warning("No tasks found from Windows Task Scheduler")
        return

    for task in tasks:
        print(task)  # 调试输出任务信息
        job_id = task.get('TaskName')
        if not job_id:
            continue

        # 检查任务是否已存在（简化检查，可扩展）
        # 这里假设有函数检查数据库


        # 解析触发器
        triggers = task.get('Triggers', '')
        trigger_info = parse_trigger(triggers)

        # 映射脚本
        script_text = task.get('Command', '') + ' ' + task.get('Arguments', '')
        script_type = 'shell'  # 默认 shell，可根据扩展名判断

        # 添加任务
        try:
            if trigger_info['type'] == 'cron':
                add_local_cron_job(
                    script_text=script_text,                      # 脚本内容
                    script_type=script_type,                      # 脚本类型
                    cron_list=trigger_info['cron_list'],          # cron 表达式
                    timeout=300,                                  # 超时时间
                    job_id=job_id, 							      # 任务 ID
                    update_by='system',                           # 更新者
                    update_datetime=datetime.now(),               # 更新时间
                    create_by='system',                           # 创建者
                    create_datetime=datetime.now(),               # 创建时间
                    extract_names=None,                           # 提取变量
                    notify_channels=None,                         # 通知渠道
                    is_pause=False                                # 是否暂停
                )
            elif trigger_info['type'] == 'interval':
                add_local_interval_job(
                    script_text=script_text,
                    script_type=script_type,
                    interval=trigger_info.get('seconds', 3600),
                    timeout=300,
                    job_id=job_id,
                    update_by='system',
                    update_datetime=datetime.now(),
                    create_by='system',
                    create_datetime=datetime.now(),
                    extract_names=None,
                    notify_channels=None,
                    is_pause=False
                )
            elif trigger_info['type'] == 'date':
                add_local_date_job(
                    script_text=script_text,
                    script_type=script_type,                      
                    timeout=300,
                    job_id=job_id,
                    extract_names=None,
                    notify_channels=None,
                    env_vars=None
                )
            logger.info(f"成功整合任务: {job_id}")
        except Exception as e:
            logger.error(f"整合任务失败: {job_id}, 错误: {e}")

if __name__ == "__main__":
    integrate_windows_tasks()