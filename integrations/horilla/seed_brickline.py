"""Enter Brickline Design Co.'s made-up data into Horilla.

Run inside Horilla (it uses Horilla's own models), with BRICKLINE_SEED pointing at data/brickline/seed.json:
    BRICKLINE_SEED=/path/to/seed.json python manage.py shell < /path/to/seed_brickline.py      # local Horilla
    docker compose cp data/brickline/seed.json web:/tmp/seed.json                              # Horilla's Docker stack
    BRICKLINE_SEED=/tmp/seed.json docker compose exec -T -e BRICKLINE_SEED web python manage.py shell < seed_brickline.py

seed.json (written by scripts/make_brickline_data.py) is the same data the spreadsheet and the Demo HRMS use:
about 85 people. BRICKLINE_STAGE says how far HR has got: june, july (the default), august, september or october.
Each stage enters what HR knew by the end of that month: people who have joined, salary records entered by then
(so a raise approved late appears only at the stage of the month it was approved), the last working day of
people leaving, leave, and attendance up to the end of the month. Run the stages in order; each adds to the
previous one, as HR would. Safe to run more than once. BRICKLINE_STAGE_FILE, if set, gets the stage name.

How Brickline maps onto Horilla:
  * employee  -> Employee (badge id = BL001...) + Work information (joining date, location; for someone
                 leaving, "Contract End Date" = the last working day. They stay active until the final settlement.)
  * salary    -> Contract, "Contract wage" = the full monthly salary; the payroll system splits it
                 50/20/30 into basic/HRA/special with the company's salary-structure setting.
                 A raise is a new contract; the previous one ends the day before and is marked expired.
  * leave     -> Leave requests; "Loss of Pay" is an unpaid leave type.
  * org       -> Departments, job positions, job roles, employee/work types, reporting managers,
                 a Mon-Fri general shift and the company holidays (shown only; pay does not use them).
  * attendance-> One validated check-in/check-out per working day from 1 April (or the joining date) to the
                 end of the stage's month, skipping weekends, holidays and approved leave, so attendance agrees
                 with the leave records the payroll system reads. Each day also gets its clock-in/clock-out entry
                 (Attendance Activities) with the same times.
"""
import json
import os
import random
from datetime import date, time, timedelta
from pathlib import Path

from attendance.models import Attendance, AttendanceActivity, AttendanceValidationCondition
from base.models import (Company, Department, EmployeeShift, EmployeeShiftDay, EmployeeShiftSchedule,
                         EmployeeType, Holidays, JobPosition, JobRole, WorkType)
from employee.models import Employee, EmployeeWorkInformation
from leave.models import LeaveRequest, LeaveType
from payroll.models.models import Contract

seed_path = os.environ.get("BRICKLINE_SEED")
if not seed_path:
    raise SystemExit("Set BRICKLINE_SEED to the path of data/brickline/seed.json")
DATA = json.loads(Path(seed_path).read_text())
HOLIDAYS = DATA.get("holidays") or [
    ["Mahavir Jayanti", "2025-04-10"],
    ["Ambedkar Jayanti", "2025-04-14"],
    ["Good Friday", "2025-04-18"],
    ["May Day", "2025-05-01"],
    ["Bakrid", "2025-06-07"],
    ["Muharram", "2025-07-06"],
    ["Independence Day", "2025-08-15"],
    ["Ganesh Chaturthi", "2025-08-27"],
    ["Gandhi Jayanti", "2025-10-02"],
    ["Diwali", "2025-10-20"],
]
ATTENDANCE_FROM = date(2025, 4, 1)
STAGE_END = {"june": "2025-06-30", "july": "2025-07-31", "august": "2025-08-31", "september": "2025-09-30", "october": "2025-10-31"}
stage = os.environ.get("BRICKLINE_STAGE", "july")
if stage not in STAGE_END:
    raise SystemExit(f"BRICKLINE_STAGE must be one of {', '.join(STAGE_END)}")
stage_end = STAGE_END[stage]
# People who join after July are entered at the stage of their joining month (everyone else is there from the start).
known_until = max(stage_end, STAGE_END["july"])
staff = {e["code"]: e for e in DATA["employees"] if e["doj"] <= known_until}
# BRICKLINE_KEEP (e.g. "BL001,BL002,...") keeps only those people in Horilla; everyone else from the seed is removed.
keep = {c.strip() for c in os.environ.get("BRICKLINE_KEEP", "").split(",") if c.strip()}
if keep:
    staff = {code: e for code, e in staff.items() if code in keep}
    from attendance.models import AttendanceOverTime

    gone = Employee.objects.filter(badge_id__startswith="BL").exclude(badge_id__in=keep)
    # Horilla protects an employee that still has these; remove them first.
    EmployeeWorkInformation.objects.filter(reporting_manager_id__in=gone).update(reporting_manager_id=None)
    for model in (AttendanceActivity, Attendance, AttendanceOverTime, Contract, LeaveRequest):
        model.objects.filter(employee_id__in=gone).delete()
    gone.delete()

company = Company.objects.filter(company="Brickline Design Co.").first()
if company is None:
    company = Company.objects.create(company="Brickline Design Co.", hq=True, address="Indiranagar", country="India",
                                     state="Karnataka", city="Bengaluru", zip="560038")

people = {}
for badge, e in staff.items():
    first, last = e["name"].split(" ", 1)
    city, state = [part.strip() for part in e["location"].split(",")]
    emp = Employee.objects.filter(badge_id=badge).first()
    if emp is None:
        emp = Employee(badge_id=badge, employee_first_name=first, employee_last_name=last,
                       gender={"F": "female", "M": "male"}.get(e["gender"], "other"),
                       email=f"{first.lower()}.{last.lower()}@brickline.example", phone=e["phone"],
                       state=state, city=city, country="India")
        emp.save()
    info, _ = EmployeeWorkInformation.objects.get_or_create(employee_id=emp)
    info.date_joining = date.fromisoformat(e["doj"])
    info.location = e["location"]
    info.company_id = company
    info.save()
    # Horilla creates an empty "<name>'s Contract" (wage 0, starting today) for a new employee; remove it.
    Contract.objects.filter(employee_id=emp, wage=0).delete()
    people[badge] = emp

# Horilla only lets a user sign in when an Employee is linked to it. Link the admin login to
# Kavya Reddy (design director) rather than adding a person who is not in the company.
from django.contrib.auth import get_user_model  # noqa: E402

admin_user = get_user_model().objects.filter(username=os.environ.get("HORILLA_ADMIN", "admin")).first()
Contract.objects.filter(employee_id__badge_id="HR001").delete()  # left over from an older version of this script
Employee.objects.filter(badge_id="HR001").delete()
if admin_user and not Employee.objects.filter(employee_user_id=admin_user).exists():
    kavya = people["BL004"]
    previous_user = kavya.employee_user_id
    Employee.objects.filter(pk=kavya.pk).update(employee_user_id=admin_user)
    if previous_user and previous_user.pk != admin_user.pk:
        previous_user.delete()

# Salary records -> contracts. A record appears at the stage of the month HR entered it ("recorded on").
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
seen = set()
for s_ in sorted(DATA["salaries"], key=lambda r: (r["code"], r["effective_from"])):
    badge, start = s_["code"], s_["effective_from"]
    if s_["recorded_on"] > stage_end or badge not in staff:
        continue
    full_name = staff[badge]["name"]
    name = (f"{full_name} revised (raise approved {MONTHS[int(s_['recorded_on'][5:7]) - 1]})" if badge in seen
            else f"{full_name} {start[:4]}")
    seen.add(badge)
    if Contract.objects.filter(contract_name=name).exists():
        continue
    # Older contract of the same person ends the day before the new one starts.
    for older in Contract.objects.filter(employee_id=people[badge], contract_end_date__isnull=True):
        if older.contract_start_date < date.fromisoformat(start):
            Contract.objects.filter(pk=older.pk).update(contract_end_date=date.fromisoformat(start) - timedelta(days=1),
                                                         contract_status="expired")
    Contract.objects.bulk_create([Contract(
        contract_name=name, employee_id=people[badge], contract_start_date=date.fromisoformat(start),
        contract_end_date=None, wage_type="monthly", pay_frequency="monthly",
        wage=s_["basic"] + s_["hra"] + s_["special"], contract_status="active",
    )])

# People leaving: the last working day goes on the work information and ends their contract.
for badge, e in staff.items():
    if e.get("doe") and e["doe"] <= stage_end:
        last = date.fromisoformat(e["doe"])
        EmployeeWorkInformation.objects.filter(employee_id=people[badge]).update(contract_end_date=last)
        Contract.objects.filter(employee_id=people[badge]).filter(contract_end_date__isnull=True).update(
            contract_end_date=last, contract_status="expired")

types = {}
for name, payment in (("Loss of Pay", "unpaid"), ("Sick Leave", "paid"), ("Casual Leave", "paid"), ("Earned Leave", "paid")):
    types[name] = LeaveType.objects.filter(name=name).first() or LeaveType.objects.create(name=name, payment=payment, total_days=12)

for lv in DATA["leave"]:
    badge, start = lv["code"], lv["from"]
    if badge not in staff or start > known_until:
        continue
    exists = LeaveRequest.objects.filter(employee_id=people[badge], start_date=start, leave_type_id=types[lv["type"]]).exists()
    if not exists:
        LeaveRequest.objects.bulk_create([LeaveRequest(
            employee_id=people[badge], leave_type_id=types[lv["type"]], start_date=date.fromisoformat(start),
            end_date=date.fromisoformat(lv["to"]), start_date_breakdown="full_day", end_date_breakdown="full_day",
            requested_days=lv["days"], status=lv["status"].lower(), description="Brickline demo data",
        )])

# ---------------------------------------------------------------- organisation
def one(model, **fields):
    obj = model.objects.filter(**fields).first() or model.objects.create(**fields)
    if hasattr(obj, "company_id") and hasattr(obj.company_id, "add"):
        obj.company_id.add(company)
    return obj


employee_type = one(EmployeeType, employee_type="Full-time")
for other in ("Part-time", "Intern", "Contract"):
    one(EmployeeType, employee_type=other)
work_types = {name: one(WorkType, work_type=name) for name in ("Work from office", "Hybrid", "Remote")}

shift = one(EmployeeShift, employee_shift="General (09:30-18:30)", weekly_full_time="42:30", full_time="180:00")
for day in EmployeeShiftDay.objects.all():
    if day.day in ("saturday", "sunday"):
        continue
    if not EmployeeShiftSchedule.objects.filter(shift_id=shift, day=day).exists():
        schedule = EmployeeShiftSchedule.objects.create(shift_id=shift, day=day, minimum_working_hour="08:30",
                                                        start_time=time(9, 30), end_time=time(18, 30))
        schedule.company_id.add(company)
# Staying a few minutes past the 8h30 minimum is not overtime; only time beyond an extra hour is.
if not AttendanceValidationCondition.objects.filter(shift_ids=shift).exists():
    condition = AttendanceValidationCondition.objects.create(
        validation_at_work="09:30", minimum_overtime_to_approve="01:00", overtime_cutoff="02:00")
    condition.shift_ids.add(shift)
    condition.company_id.add(company)

for badge, e in staff.items():
    dept = one(Department, department=e["department"])
    position = one(JobPosition, job_position=e["designation"], department_id=dept)
    role = one(JobRole, job_role=e["job_role"], job_position_id=position)
    EmployeeWorkInformation.objects.filter(employee_id=people[badge]).update(
        department_id=dept, job_position_id=position, job_role_id=role, shift_id=shift,
        work_type_id=work_types[e["work_type"]], employee_type_id=employee_type,
        reporting_manager_id=people.get(e["manager"]) if e["manager"] else None,
        email=people[badge].email, mobile=e["phone"],
    )
    Employee.objects.filter(pk=people[badge].pk).update(
        dob=date.fromisoformat(e["dob"]), marital_status=e["marital_status"], children=e["children"],
        qualification=e["qualification"], experience=e["experience"], address=e["address"], zip=e["pin"], phone=e["phone"],
    )

holidays = set()
for name, day in HOLIDAYS:
    day = date.fromisoformat(day)
    holidays.add(day)
    if not Holidays.objects.filter(start_date=day, company_id=company).exists():
        Holidays.objects.create(name=name, start_date=day, end_date=day, company_id=company)

# ---------------------------------------------------------------- attendance
on_leave = {}
for lv in DATA["leave"]:
    badge = lv["code"]
    if badge in staff and lv["from"] <= known_until and lv["status"].lower() == "approved":
        day = date.fromisoformat(lv["from"])
        while day <= date.fromisoformat(lv["to"]):
            on_leave.setdefault(badge, set()).add(day)
            day += timedelta(days=1)

def clock(minutes):
    return time(*divmod(minutes, 60))


# Days with no check-in although nobody approved leave: absent without asking, or a forgotten punch (seed.json).
for gap in DATA.get("attendance_gaps", []):
    if gap["code"] in staff:
        on_leave.setdefault(gap["code"], set()).add(date.fromisoformat(gap["date"]))
no_card = {(x["code"], x["month"]) for x in DATA.get("no_attendance", [])}

day_rows = {d.day: d for d in EmployeeShiftDay.objects.all()}
for badge, emp in people.items():
    last_day = date.fromisoformat(min(stage_end, staff[badge].get("doe") or stage_end))
    # Attendance must agree with leave: nobody is marked present on a day of approved leave (or a day in the gaps).
    Attendance.objects.filter(employee_id=emp, attendance_date__in=on_leave.get(badge, ())).delete()
    AttendanceActivity.objects.filter(employee_id=emp, attendance_date__in=on_leave.get(badge, ())).delete()
    for code, month in no_card:
        if code == badge:
            Attendance.objects.filter(employee_id=emp, attendance_date__startswith=month).delete()
            AttendanceActivity.objects.filter(employee_id=emp, attendance_date__startswith=month).delete()
    have = set(Attendance.objects.filter(employee_id=emp).values_list("attendance_date", flat=True))
    work_type = work_types[staff[badge]["work_type"]]
    day = max(ATTENDANCE_FROM, date.fromisoformat(staff[badge]["doj"]))
    while day <= last_day:
        working = (day.weekday() < 5 and day not in holidays and day not in on_leave.get(badge, ())
                   and (badge, day.isoformat()[:7]) not in no_card)
        if working and day not in have:
            rng = random.Random(f"{badge}{day}")  # same times every run
            clock_in = 9 * 60 + rng.randint(0, 45)  # 09:00-09:45
            worked = 8 * 60 + rng.randint(40, 85)   # 8h40-9h25, always above the 8h30 minimum
            Attendance(
                employee_id=emp, attendance_date=day, shift_id=shift, work_type_id=work_type,
                attendance_day=day_rows[day.strftime("%A").lower()],
                attendance_clock_in_date=day, attendance_clock_in=clock(clock_in),
                attendance_clock_out_date=day, attendance_clock_out=clock(clock_in + worked),
                attendance_worked_hour="%02d:%02d" % divmod(worked, 60),
                minimum_hour="08:30", attendance_validated=True,
            ).save()
        day += timedelta(days=1)

# The clock-in/clock-out entry behind each attendance day (Horilla's Attendance Activities), with the same times.
from datetime import datetime  # noqa: E402

from django.conf import settings as horilla_settings  # noqa: E402
from django.utils import timezone  # noqa: E402


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
    if (a.employee_id_id, a.attendance_date) not in have_activity
], batch_size=1000)

if os.environ.get("BRICKLINE_STAGE_FILE"):
    Path(os.environ["BRICKLINE_STAGE_FILE"]).write_text(stage + "\n")
if os.environ.get("BRICKLINE_PEOPLE_FILE"):  # who is in Horilla (read by the tests); empty means everyone in the seed
    Path(os.environ["BRICKLINE_PEOPLE_FILE"]).write_text(",".join(sorted(keep)) + "\n")
print(json.dumps({"stage": stage, "employees": len(people),
                  "leaving": sorted(b for b, e in staff.items() if e.get("doe") and e["doe"] <= stage_end), "contracts": Contract.objects.filter(employee_id__in=people.values()).count(),
                  "leave_requests": LeaveRequest.objects.filter(employee_id__in=people.values()).count(),
                  "departments": len({e["department"] for e in staff.values()}), "holidays": len(holidays),
                  "attendance": Attendance.objects.filter(employee_id__in=people.values()).count(),
                  "clock_in_entries": AttendanceActivity.objects.filter(employee_id__in=people.values()).count()}))
