"""Enter prosperric acid's made-up data into Horilla: the Kestrel sample people, and what HR enters for December 2026.

Run inside Horilla (it uses Horilla's own models), on its own database so the company's lists hold only its people:
    PROSPERRIC_SEED=/path/to/data/kestrel/seed.json DB_NAME=/path/to/prosperric.sqlite3 python manage.py shell < seed_prosperric.py
or simply: ./scripts/horilla.sh prosperric setup

The people, salaries and November leave are data/kestrel/seed.json (written by scripts/make_kestrel_data.py). On top,
HR's December, the month after November was paid (each case shows a part of the pay groups and the cycles):

  * KS006 Rohan Iyer joins Engineering on 7 Dec (₹55,000): the Shift staff group takes him by its department rule.
  * KS002 Meera Joshi: half a day unpaid on 15 Dec (Horilla: Second Half), 0.5 day.
  * KS003 Sameer Kulkarni: unpaid 29-30 Dec, after the 25th: on a 26th-to-25th count it falls in January's pay.
  * KS001 Arvind Rao: unpaid on 27 Nov. November was paid with unpaid days counted up to 25 Nov, so December
    (Management counts the calendar month) counts it, as part of the move to the new cycle.
  * KS005 Anjali Desai: sick leave on 10 Dec, paid: no cut.

How it sits in Horilla: an Employee per person (badge id = KS001...) with work information (department, job position,
joining date, location), a Contract per salary record ("Contract wage" = the full monthly salary; a raise is a new
contract, the previous one ending the day before), Leave requests with Horilla's own Start/End Date Breakdown
(Full Day / First Half / Second Half) and a validated check-in per working day of November up to the 25th.
Safe to run more than once.
"""
import json
import os
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

from attendance.models import Attendance, AttendanceActivity
from base.models import Company, Department, EmployeeShift, EmployeeShiftDay, EmployeeShiftSchedule, EmployeeType, Holidays, JobPosition
from django.conf import settings as horilla_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from employee.models import Employee, EmployeeWorkInformation
from leave.models import LeaveRequest, LeaveType
from payroll.models.models import Contract

seed_path = os.environ.get("PROSPERRIC_SEED")
if not seed_path:
    raise SystemExit("Set PROSPERRIC_SEED to the path of data/kestrel/seed.json")
DATA = json.loads(Path(seed_path).read_text())

DECEMBER_PEOPLE = [{"code": "KS006", "name": "Rohan Iyer", "gender": "M", "location": "Bengaluru, Karnataka", "doj": "2026-12-07",
                    "department": "Engineering", "designation": "Data Engineer"}]
DECEMBER_SALARIES = [{"code": "KS006", "effective_from": "2026-12-07", "basic": 27500, "hra": 11000, "special_allowance": 16500}]
DECEMBER_LEAVE = [
    {"code": "KS002", "type": "Loss of Pay", "from": "2026-12-15", "start_breakdown": "Second Half", "to": "2026-12-15",
     "end_breakdown": "Second Half", "days": 0.5, "status": "Approved"},
    {"code": "KS003", "type": "Loss of Pay", "from": "2026-12-29", "start_breakdown": "Full Day", "to": "2026-12-30",
     "end_breakdown": "Full Day", "days": 2, "status": "Approved"},
    {"code": "KS001", "type": "Loss of Pay", "from": "2026-11-27", "start_breakdown": "Full Day", "to": "2026-11-27",
     "end_breakdown": "Full Day", "days": 1, "status": "Approved"},
    {"code": "KS005", "type": "Sick Leave", "from": "2026-12-10", "start_breakdown": "Full Day", "to": "2026-12-10",
     "end_breakdown": "Full Day", "days": 1, "status": "Approved"},
]
HOLIDAYS = [["Kannada Rajyotsava", "2026-11-01"], ["Christmas", "2026-12-25"]]
ATTENDANCE = (date(2026, 11, 2), date(2026, 11, 25))  # November up to today's cut-off (attendance is not entered ahead)
BREAKDOWN = {"Full Day": "full_day", "First Half": "first_half", "Second Half": "second_half"}

staff = {e["code"]: e for e in DATA["employees"] + DECEMBER_PEOPLE}
salaries = DATA["salaries"] + [dict(s, recorded_on=s["effective_from"]) for s in DECEMBER_SALARIES]
leave = DATA["leave"] + DECEMBER_LEAVE

company = Company.objects.filter(company="Prosperric Acid").first() or Company.objects.create(
    company="Prosperric Acid", hq=True, address="Koramangala", country="India", state="Karnataka", city="Bengaluru", zip="560034")


def one(model, **fields):
    obj = model.objects.filter(**fields).first() or model.objects.create(**fields)
    if hasattr(obj, "company_id") and hasattr(obj.company_id, "add"):
        obj.company_id.add(company)
    return obj


people = {}
for badge, e in staff.items():
    first, last = e["name"].split(" ", 1)
    city, state = [part.strip() for part in e["location"].split(",")]
    emp = Employee.objects.filter(badge_id=badge).first()
    if emp is None:
        emp = Employee(badge_id=badge, employee_first_name=first, employee_last_name=last,
                       gender={"F": "female", "M": "male"}.get(e["gender"], "other"),
                       email=f"{first.lower()}.{last.lower()}@prosperric.example", phone=f"98450{int(badge[2:]):05d}",
                       state=state, city=city, country="India")
        emp.save()
    Contract.objects.filter(employee_id=emp, wage=0).delete()  # Horilla's empty contract for a new employee
    people[badge] = emp

employee_type = one(EmployeeType, employee_type="Full-time")
shift = one(EmployeeShift, employee_shift="General (09:30-18:30)", weekly_full_time="42:30", full_time="180:00")
for day in EmployeeShiftDay.objects.all():
    if day.day not in ("saturday", "sunday") and not EmployeeShiftSchedule.objects.filter(shift_id=shift, day=day).exists():
        schedule = EmployeeShiftSchedule.objects.create(shift_id=shift, day=day, minimum_working_hour="08:30",
                                                        start_time=time(9, 30), end_time=time(18, 30))
        schedule.company_id.add(company)
for badge, e in staff.items():
    dept = one(Department, department=e["department"])
    position = one(JobPosition, job_position=e["designation"], department_id=dept)
    info, _ = EmployeeWorkInformation.objects.get_or_create(employee_id=people[badge])
    EmployeeWorkInformation.objects.filter(pk=info.pk).update(
        date_joining=date.fromisoformat(e["doj"]), location=e["location"], company_id=company, department_id=dept,
        job_position_id=position, shift_id=shift, employee_type_id=employee_type, email=people[badge].email,
        reporting_manager_id=people["KS001"] if badge != "KS001" else None)

# Horilla lets a user sign in only when an Employee is linked to it: the admin login is Arvind Rao, the director.
admin_user = get_user_model().objects.filter(username=os.environ.get("HORILLA_ADMIN", "admin")).first()
if admin_user and not Employee.objects.filter(employee_user_id=admin_user).exists():
    arvind = people["KS001"]
    previous_user = arvind.employee_user_id
    Employee.objects.filter(pk=arvind.pk).update(employee_user_id=admin_user)
    if previous_user and previous_user.pk != admin_user.pk:
        previous_user.delete()

# Salary records -> contracts; a raise is a new contract and the previous one ends the day before.
seen = set()
for s_ in sorted(salaries, key=lambda r: (r["code"], r["effective_from"])):
    badge, start = s_["code"], date.fromisoformat(s_["effective_from"])
    name = f"{staff[badge]['name']} {'revised ' if badge in seen else ''}{start:%b %Y}"
    seen.add(badge)
    if Contract.objects.filter(contract_name=name).exists():
        continue
    for older in Contract.objects.filter(employee_id=people[badge], contract_end_date__isnull=True):
        if older.contract_start_date < start:
            Contract.objects.filter(pk=older.pk).update(contract_end_date=start - timedelta(days=1), contract_status="expired")
    Contract.objects.bulk_create([Contract(
        contract_name=name, employee_id=people[badge], contract_start_date=start, contract_end_date=None, wage_type="monthly",
        pay_frequency="monthly", wage=s_["basic"] + s_["hra"] + s_["special_allowance"], contract_status="active")])

types = {name: LeaveType.objects.filter(name=name).first() or LeaveType.objects.create(name=name, payment=payment, total_days=12)
         for name, payment in (("Loss of Pay", "unpaid"), ("Sick Leave", "paid"), ("Casual Leave", "paid"))}
for lv in leave:
    start = date.fromisoformat(lv["from"])
    if not LeaveRequest.objects.filter(employee_id=people[lv["code"]], start_date=start, leave_type_id=types[lv["type"]]).exists():
        LeaveRequest.objects.bulk_create([LeaveRequest(
            employee_id=people[lv["code"]], leave_type_id=types[lv["type"]], start_date=start, end_date=date.fromisoformat(lv["to"]),
            start_date_breakdown=BREAKDOWN[lv.get("start_breakdown", "Full Day")], end_date_breakdown=BREAKDOWN[lv.get("end_breakdown", "Full Day")],
            requested_days=lv["days"], status=lv["status"].lower(), description="prosperric acid demo data")])

holidays = set()
for name, day in HOLIDAYS:
    day = date.fromisoformat(day)
    holidays.add(day)
    if not Holidays.objects.filter(start_date=day, company_id=company).exists():
        Holidays.objects.create(name=name, start_date=day, end_date=day, company_id=company)

# A validated check-in per working day of November, except on approved leave.
on_leave = {}
for lv in leave:
    if lv["status"] == "Approved":
        day = date.fromisoformat(lv["from"])
        while day <= date.fromisoformat(lv["to"]):
            on_leave.setdefault(lv["code"], set()).add(day)
            day += timedelta(days=1)
day_rows = {d.day: d for d in EmployeeShiftDay.objects.all()}
for badge, emp in people.items():
    have = set(Attendance.objects.filter(employee_id=emp).values_list("attendance_date", flat=True))
    day = max(ATTENDANCE[0], date.fromisoformat(staff[badge]["doj"]))
    while day <= ATTENDANCE[1]:
        if day.weekday() < 5 and day not in holidays and day not in on_leave.get(badge, ()) and day not in have:
            rng = random.Random(f"{badge}{day}")
            clock_in = 9 * 60 + rng.randint(0, 45)
            worked = 8 * 60 + rng.randint(40, 85)
            Attendance(employee_id=emp, attendance_date=day, shift_id=shift, attendance_day=day_rows[day.strftime("%A").lower()],
                       attendance_clock_in_date=day, attendance_clock_in=time(*divmod(clock_in, 60)),
                       attendance_clock_out_date=day, attendance_clock_out=time(*divmod(clock_in + worked, 60)),
                       attendance_worked_hour="%02d:%02d" % divmod(worked, 60), minimum_hour="08:30",
                       attendance_validated=True).save()
        day += timedelta(days=1)


def moment(day, at):
    value = datetime.combine(day, at)
    return timezone.make_aware(value) if horilla_settings.USE_TZ else value


have_activity = set(AttendanceActivity.objects.filter(employee_id__in=people.values()).values_list("employee_id", "attendance_date"))
AttendanceActivity.objects.bulk_create([
    AttendanceActivity(employee_id_id=a.employee_id_id, attendance_date=a.attendance_date, shift_day=a.attendance_day,
                       clock_in_date=a.attendance_clock_in_date, clock_in=a.attendance_clock_in,
                       in_datetime=moment(a.attendance_clock_in_date, a.attendance_clock_in),
                       clock_out_date=a.attendance_clock_out_date, clock_out=a.attendance_clock_out,
                       out_datetime=moment(a.attendance_clock_out_date, a.attendance_clock_out))
    for a in Attendance.objects.filter(employee_id__in=people.values()).order_by("attendance_date")
    if (a.employee_id_id, a.attendance_date) not in have_activity])

print(json.dumps({"company": company.company, "employees": len(people), "contracts": Contract.objects.filter(employee_id__in=people.values()).count(),
                  "leave_requests": LeaveRequest.objects.filter(employee_id__in=people.values()).count(),
                  "attendance": Attendance.objects.filter(employee_id__in=people.values()).count()}))
