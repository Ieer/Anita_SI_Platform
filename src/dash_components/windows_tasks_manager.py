"""Windows 任务调度器管理器
提供 Windows 任务调度器的操作接口
"""
import subprocess
import json
import re
from datetime import datetime

class WindowsTasksManager:
    def __init__(self):
        pass

    def open_task_scheduler(self):
        command = ['control', 'schedtasks']
        output = self.run_command(command)
        return output

    def run_command(self, command):
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
            return result.stdout
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f'Windows task command failed ({error.returncode}): {error.stderr or error.stdout}') from error

    def validate_task(self, task_name):
        if not isinstance(task_name, str) or task_name not in {task['TaskName'] for task in self.get_scheduled_tasks()}:
            raise ValueError('Task is not available to the current Windows account')

    def pause_task(self, task_name):
        self.validate_task(task_name)
        command = ['schtasks', '/Change', '/TN', task_name, '/Disable']
        output = self.run_command(command)
        return output

    def ready_task(self, task_name):
        self.validate_task(task_name)
        command = ['schtasks', '/Change', '/TN', task_name, '/Enable']
        output = self.run_command(command)
        return output

    def start_task(self, task_name):
        self.validate_task(task_name)
        command = ['schtasks', '/Run', '/TN', task_name]
        output = self.run_command(command)
        return output

    def delete_task(self, task_name):
        self.validate_task(task_name)
        command = ['schtasks', '/Delete', '/TN', task_name, '/F']
        output = self.run_command(command)
        return output

    @staticmethod
    def _as_list(value):
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @staticmethod
    def _parse_boundary(value):
        if not value:
            return None
        normalized = value.replace('Z', '+00:00')
        return datetime.fromisoformat(normalized)

    @staticmethod
    def _weekday_names(mask):
        days = ((2, 'MON'), (4, 'TUE'), (8, 'WED'), (16, 'THU'), (32, 'FRI'), (64, 'SAT'), (1, 'SUN'))
        return ','.join(name for bit, name in days if mask & bit)

    @staticmethod
    def _month_names(mask):
        return ','.join(
            name
            for bit, name in enumerate(
                ('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'),
                start=1,
            )
            if mask & (1 << (bit - 1))
        )

    @classmethod
    def _normalize_import_task(cls, task):
        row = dict(task)
        triggers = cls._as_list(task.get('Triggers'))
        actions = cls._as_list(task.get('Actions'))
        schedule_type = 'Unsupported'
        start_time = ''
        start_date = ''
        start_datetime = ''
        end_date = 'N/A'
        end_datetime = ''
        days = ''
        months = ''
        issues = []

        if len(triggers) != 1:
            issues.append('requires exactly one trigger')
        else:
            trigger = triggers[0]
            trigger_type = trigger.get('Type')
            start_boundary = cls._parse_boundary(trigger.get('StartBoundary'))
            end_boundary = cls._parse_boundary(trigger.get('EndBoundary'))
            if start_boundary is None:
                issues.append('trigger has no start boundary')
            elif start_boundary.utcoffset() is not None:
                issues.append('trigger uses an explicit time zone')
            else:
                start_time = start_boundary.strftime('%H:%M:%S')
                start_date = start_boundary.strftime('%Y/%m/%d')
                start_datetime = start_boundary.isoformat()
                end_date = end_boundary.strftime('%Y/%m/%d') if end_boundary else 'N/A'
                end_datetime = end_boundary.isoformat() if end_boundary else ''

            if trigger_type == 'MSFT_TaskDailyTrigger':
                schedule_type = 'Daily'
                if int(trigger.get('DaysInterval') or 1) != 1:
                    issues.append('daily interval is greater than one day')
            elif trigger_type == 'MSFT_TaskWeeklyTrigger':
                schedule_type = 'Weekly'
                days = cls._weekday_names(int(trigger.get('DaysOfWeek') or 0))
                if not days:
                    issues.append('weekly trigger has no weekdays')
                if int(trigger.get('WeeksInterval') or 1) != 1:
                    issues.append('weekly interval is greater than one week')
            elif trigger_type == 'MSFT_TaskMonthlyTrigger':
                schedule_type = 'Monthly'
                if trigger.get('RunOnLastDayOfMonth') or trigger.get('RunOnDayOfWeek'):
                    issues.append('monthly ordinal or last-day trigger is unsupported')
                days = ','.join(str(day) for day in cls._as_list(trigger.get('DaysOfMonth')))
                months = cls._month_names(int(trigger.get('MonthsOfYear') or 0))
                if not days or not months:
                    issues.append('monthly trigger has no supported days or months')
            else:
                issues.append(f'unsupported trigger type: {trigger_type or "unknown"}')

            if trigger.get('RepetitionInterval'):
                issues.append('repeating trigger is unsupported')

        task_to_run = ''
        start_in = ''
        if len(actions) != 1 or actions[0].get('Type') != 'MSFT_TaskExecAction':
            issues.append('requires exactly one executable action')
        else:
            action = actions[0]
            executable = action.get('Execute') or ''
            if not executable:
                issues.append('action has no executable')
            else:
                task_to_run = subprocess.list2cmdline([executable])
                arguments = action.get('Arguments') or ''
                if arguments:
                    task_to_run = f'{task_to_run} {arguments}'
                start_in = action.get('WorkingDirectory') or ''

        execution_limit = task.get('ExecutionTimeLimit') or ''
        limit_match = re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?', execution_limit)
        execution_seconds = 300
        if limit_match:
            days_limit, hours, minutes, seconds = limit_match.groups()
            execution_seconds = int(
                int(days_limit or 0) * 86400
                + int(hours or 0) * 3600
                + int(minutes or 0) * 60
                + float(seconds or 0)
            )

        try:
            last_run = datetime.strptime(task.get('Last Run Time', ''), '%Y-%m-%d %H:%M:%S')
            last_run_time = last_run.strftime('%m/%d/%Y %I:%M:%S %p')
        except (TypeError, ValueError):
            last_run_time = 'N/A'

        row.update({
            'Schedule Type': schedule_type,
            'Start Time': start_time,
            'Start Date': start_date,
            'Start DateTime': start_datetime,
            'End Date': end_date,
            'End DateTime': end_datetime,
            'Days': days,
            'Months': months,
            'Task To Run': task_to_run,
            'Start In': start_in,
            'HostName': task.get('HostName') or '',
            'Last Run Time': last_run_time,
            'Last Result': task.get('Last Result', ''),
            'Execution Time': execution_seconds,
            'Import Supported': not issues,
            'Import Issue': '; '.join(issues),
        })
        return row

    def get_scheduled_tasks(self):
        script = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$account = $identity.Name
$sid = $identity.User.Value
$rows = @(Get-ScheduledTask | Where-Object {
    $_.Principal.UserId -in @($account, $sid, $env:USERNAME)
} | ForEach-Object {
    $info = $_ | Get-ScheduledTaskInfo
        $triggers = @($_.Triggers | ForEach-Object {
            [ordered]@{
                'Type' = $_.CimClass.CimClassName
                'StartBoundary' = [string]$_.StartBoundary
                'EndBoundary' = [string]$_.EndBoundary
                'DaysInterval' = $_.DaysInterval
                'WeeksInterval' = $_.WeeksInterval
                'DaysOfWeek' = [int]$_.DaysOfWeek
                'DaysOfMonth' = @($_.DaysOfMonth | ForEach-Object { [int]$_ })
                'MonthsOfYear' = [int]$_.MonthsOfYear
                'RunOnLastDayOfMonth' = $_.RunOnLastDayOfMonth
                'RunOnDayOfWeek' = $_.RunOnDayOfWeek
                'RepetitionInterval' = [string]$_.Repetition.Interval
            }
        })
        $actions = @($_.Actions | ForEach-Object {
            [ordered]@{
                'Type' = $_.CimClass.CimClassName
                'Execute' = [string]$_.Execute
                'Arguments' = [string]$_.Arguments
                'WorkingDirectory' = [string]$_.WorkingDirectory
            }
        })
        $executionTimeLimit = 300
        if ($_.Settings.ExecutionTimeLimit) {
            $executionTimeLimit = [System.Xml.XmlConvert]::ToTimeSpan($_.Settings.ExecutionTimeLimit).TotalSeconds
        }
    [ordered]@{
        'TaskName' = $_.TaskPath + $_.TaskName
        'Status' = [string]$_.State
        'Next Run Time' = $(if ($info.NextRunTime) { $info.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss') } else { '' })
        'Last Run Time' = $(if ($info.LastRunTime) { $info.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') } else { '' })
        'Last Result' = [string]$info.LastTaskResult
        'Author' = $_.Author
        'Principal' = [string]$_.Principal.UserId
        'MultipleInstancesPolicy' = [string]$_.Settings.MultipleInstances
            'HostName' = $env:COMPUTERNAME
            'Triggers' = $triggers
            'Actions' = $actions
            'ExecutionTimeLimit' = [System.Xml.XmlConvert]::ToString([TimeSpan]::FromSeconds($executionTimeLimit))
    }
})
    ConvertTo-Json -InputObject $rows -Depth 8 -Compress
"""
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, text=True, encoding='utf-8', check=True, timeout=30,
        )
        tasks = json.loads(result.stdout.lstrip('\ufeff'))
        tasks = tasks if isinstance(tasks, list) else [tasks]
        return [self._normalize_import_task(task) for task in tasks]

# 创建全局实例
windows_tasks_manager = WindowsTasksManager()