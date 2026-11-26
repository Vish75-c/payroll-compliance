"""Write data/brickline/seed.json: Brickline Design Co., about 80 made-up people.

The six people the answer sheets check (BL001-BL006: Priya, Arjun, Meera, Kavya, Ravi, Sneha) are kept exactly
as they are. Around them it adds 74 more, the same on every run (fixed random seed), so the demo looks like a
real small company:

* seven departments with job positions, roles and reporting managers;
* staff in Bengaluru (Karnataka) and in Mumbai and Pune (Maharashtra), so two states' professional tax apply;
* five people who join during the year (pay for part of a month);
* raises in July recorded on time, and three raises recorded late (paid as arrears in July);
* about 45 leave requests from April to July: casual, sick, earned and unpaid, some rejected or still pending.

seed.json feeds the spreadsheet (scripts/make_brickline_spreadsheet.py), the Demo HRMS and Horilla's seed.
"""
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/brickline/seed.json"

COMPANY = {"code": "brickline", "name": "Brickline Design Co.", "city": "Bengaluru", "state": "KA"}

# The six people the answer sheets check, with the details Horilla shows for them. Never change these.
CORE_EMPLOYEES = [
    {"code": "BL001", "name": "Priya Sharma", "gender": "F", "location": "Bengaluru, Karnataka", "doj": "2021-06-14",
     "department": "Design", "designation": "Senior Designer", "job_role": "Brand & Identity Lead", "work_type": "Hybrid",
     "manager": "BL004", "dob": "1993-02-11", "marital_status": "married", "children": 0, "qualification": "B.Des, NID Ahmedabad",
     "experience": 9, "address": "12th Main, HAL 2nd Stage, Indiranagar", "pin": "560038", "phone": "9845012001"},
    {"code": "BL002", "name": "Arjun Mehta", "gender": "M", "location": "Bengaluru, Karnataka", "doj": "2024-02-01",
     "department": "Studio Ops", "designation": "Studio Assistant", "job_role": "Production Support", "work_type": "Work from office",
     "manager": "BL005", "dob": "2001-09-03", "marital_status": "single", "children": 0, "qualification": "Diploma in Fine Arts",
     "experience": 1, "address": "6th Block, Koramangala", "pin": "560095", "phone": "9845012002"},
    {"code": "BL003", "name": "Meera Iyer", "gender": "F", "location": "Bengaluru, Karnataka", "doj": "2023-08-07",
     "department": "Design", "designation": "Junior Designer", "job_role": "Visual Designer", "work_type": "Hybrid",
     "manager": "BL001", "dob": "1999-12-20", "marital_status": "single", "children": 0, "qualification": "B.Des, Srishti Manipal",
     "experience": 2, "address": "4th Block, Jayanagar", "pin": "560011", "phone": "9845012003"},
    {"code": "BL004", "name": "Kavya Reddy", "gender": "F", "location": "Bengaluru, Karnataka", "doj": "2019-04-01",
     "department": "Leadership", "designation": "Design Director", "job_role": "Creative Head", "work_type": "Hybrid",
     "manager": None, "dob": "1986-05-08", "marital_status": "married", "children": 1, "qualification": "M.Des, IDC IIT Bombay",
     "experience": 16, "address": "ITPL Main Road, Whitefield", "pin": "560066", "phone": "9845012004"},
    {"code": "BL005", "name": "Ravi Kumar", "gender": "M", "location": "Bengaluru, Karnataka", "doj": "2024-11-18",
     "department": "Studio Ops", "designation": "Office Coordinator", "job_role": "Office Administration", "work_type": "Work from office",
     "manager": "BL004", "dob": "1997-07-15", "marital_status": "single", "children": 0, "qualification": "B.Com, Bangalore University",
     "experience": 4, "address": "2nd Stage, BTM Layout", "pin": "560076", "phone": "9845012005"},
    {"code": "BL006", "name": "Sneha Rao", "gender": "F", "location": "Bengaluru, Karnataka", "doj": "2022-01-10",
     "department": "Client Services", "designation": "Account Executive", "job_role": "Client Relationship", "work_type": "Work from office",
     "manager": "BL004", "dob": "1996-03-27", "marital_status": "married", "children": 0, "qualification": "MBA, Christ University",
     "experience": 5, "address": "18th Cross, Malleshwaram", "pin": "560003", "phone": "9845012006"},
]
CORE_SALARIES = [
    {"code": "BL001", "effective_from": "2025-04-01", "basic": 40000, "hra": 16000, "special": 24000, "recorded_on": "2025-03-25"},
    {"code": "BL001", "effective_from": "2025-05-01", "basic": 45000, "hra": 18000, "special": 27000, "recorded_on": "2025-07-03",
     "note": "Raise approved late: recorded in July, effective 1 May"},
    {"code": "BL002", "effective_from": "2025-04-01", "basic": 9000, "hra": 3600, "special": 5400, "recorded_on": "2025-03-25"},
    {"code": "BL003", "effective_from": "2025-04-01", "basic": 15000, "hra": 6000, "special": 9000, "recorded_on": "2025-03-25"},
    {"code": "BL004", "effective_from": "2025-04-01", "basic": 75000, "hra": 30000, "special": 45000, "recorded_on": "2025-03-25"},
    {"code": "BL005", "effective_from": "2025-04-01", "basic": 10500, "hra": 4200, "special": 6300, "recorded_on": "2025-03-25"},
    {"code": "BL006", "effective_from": "2025-04-01", "basic": 12500, "hra": 5000, "special": 7500, "recorded_on": "2025-03-25"},
]
CORE_LEAVE = [
    {"code": "BL004", "type": "Sick Leave", "from": "2025-06-09", "to": "2025-06-10", "days": 2, "status": "Approved"},
    {"code": "BL005", "type": "Loss of Pay", "from": "2025-06-16", "to": "2025-06-16", "days": 1, "status": "Approved"},
    {"code": "BL002", "type": "Loss of Pay", "from": "2025-07-14", "to": "2025-07-16", "days": 3, "status": "Approved"},
    {"code": "BL003", "type": "Casual Leave", "from": "2025-07-21", "to": "2025-07-22", "days": 2, "status": "Approved"},
    {"code": "BL006", "type": "Loss of Pay", "from": "2025-07-28", "to": "2025-07-28", "days": 1, "status": "Rejected"},
]

# Karnataka public holidays April-July 2025 (also entered in Horilla); leave never falls on them.
HOLIDAYS = {date(2025, 4, 10), date(2025, 4, 14), date(2025, 4, 18), date(2025, 5, 1), date(2025, 6, 7), date(2025, 7, 6)}

# First names are unique and never one of the six above, so "Give Priya a bonus" still names one person.
FEMALE = ["Ananya", "Diya", "Isha", "Kriti", "Lavanya", "Nandini", "Pooja", "Riya", "Shruti", "Tanvi", "Aditi", "Bhavna",
          "Charu", "Deepika", "Esha", "Gauri", "Harini", "Ishita", "Jyoti", "Lakshmi", "Madhuri", "Neha", "Pallavi",
          "Radhika", "Sanjana", "Swati", "Trisha", "Vaishnavi", "Yamini", "Zoya", "Anjali", "Divya", "Ritu", "Shalini",
          "Nikita", "Mansi", "Rashmi"]
MALE = ["Aditya", "Akash", "Aman", "Ankit", "Bharat", "Chetan", "Deepak", "Dev", "Farhan", "Gaurav", "Harsh", "Imran",
        "Karan", "Kunal", "Manish", "Naveen", "Nikhil", "Omkar", "Pranav", "Rahul", "Rohan", "Sachin", "Sameer",
        "Siddharth", "Tarun", "Uday", "Varun", "Vikram", "Vivek", "Yash", "Abhishek", "Suresh", "Ajay", "Mohit",
        "Prakash", "Rajesh", "Tejas"]
LAST = ["Nair", "Menon", "Pillai", "Patil", "Deshpande", "Kulkarni", "Joshi", "Gupta", "Verma", "Singh", "Shetty", "Hegde",
        "Kamath", "Bhat", "Naidu", "Krishnan", "Subramanian", "Das", "Banerjee", "Chatterjee", "Ghosh", "Mishra", "Pandey",
        "Saxena", "Agarwal", "Jain", "Kapoor", "Malhotra", "Khanna", "Chopra", "Desai", "Shah", "Thakur", "Fernandes",
        "Pinto", "Mathew", "Thomas", "George", "Varghese", "Ansari", "Qureshi", "Khan", "Siddiqui", "Bose", "Sen",
        "Dutta", "Gowda", "Shenoy", "Prabhu"]

# department, designation, job role, how many, monthly gross band (Rs), work type, who they report to
# ("L:<designation>" = someone with that designation, picked in order; "BL004" = Kavya)
PLAN = [
    ("Leadership", "Chief Operating Officer", "Operations Head", 1, (200000, 230000), "Hybrid", None),
    ("Leadership", "Chief Technology Officer", "Technology Head", 1, (220000, 260000), "Hybrid", None),
    ("Design", "Design Lead", "Design Team Lead", 2, (115000, 140000), "Hybrid", "BL004"),
    ("Design", "Senior Designer", "Product Designer", 4, (70000, 95000), "Hybrid", "L:Design Lead"),
    ("Design", "Designer", "Visual Designer", 7, (40000, 60000), "Hybrid", "L:Senior Designer"),
    ("Design", "Junior Designer", "Visual Designer", 5, (22000, 34000), "Hybrid", "L:Senior Designer"),
    ("Design", "Design Intern", "Design Trainee", 2, (12000, 15000), "Work from office", "L:Design Lead"),
    ("Engineering", "Engineering Manager", "Engineering Lead", 1, (160000, 190000), "Hybrid", "L:Chief Technology Officer"),
    ("Engineering", "Senior Developer", "Full-stack Developer", 4, (95000, 130000), "Remote", "L:Engineering Manager"),
    ("Engineering", "Developer", "Full-stack Developer", 7, (55000, 85000), "Hybrid", "L:Engineering Manager"),
    ("Engineering", "UI Developer", "Frontend Developer", 1, (45000, 65000), "Hybrid", "L:Engineering Manager"),
    ("Engineering", "QA Analyst", "Quality Assurance", 3, (32000, 48000), "Hybrid", "L:Engineering Manager"),
    ("Client Services", "Account Director", "Client Partner", 1, (120000, 150000), "Work from office", "L:Chief Operating Officer"),
    ("Client Services", "Account Manager", "Client Relationship", 4, (60000, 85000), "Work from office", "L:Account Director"),
    ("Client Services", "Account Executive", "Client Relationship", 5, (25000, 40000), "Work from office", "L:Account Manager"),
    ("Studio Ops", "Studio Manager", "Studio Operations", 1, (55000, 70000), "Work from office", "L:Chief Operating Officer"),
    ("Studio Ops", "Office Coordinator", "Office Administration", 2, (21000, 28000), "Work from office", "L:Studio Manager"),
    ("Studio Ops", "Studio Assistant", "Production Support", 3, (15000, 20000), "Work from office", "L:Studio Manager"),
    ("Studio Ops", "Facilities Associate", "Facilities", 2, (13000, 17000), "Work from office", "L:Studio Manager"),
    ("Finance & Admin", "Finance Manager", "Finance Lead", 1, (95000, 120000), "Work from office", "L:Chief Operating Officer"),
    ("Finance & Admin", "Accountant", "Accounts", 3, (35000, 50000), "Work from office", "L:Finance Manager"),
    ("Finance & Admin", "HR Executive", "People Operations", 2, (30000, 42000), "Work from office", "L:Finance Manager"),
    ("Finance & Admin", "Payroll Associate", "Payroll", 2, (25000, 32000), "Work from office", "L:Finance Manager"),
    ("Marketing", "Marketing Manager", "Marketing Lead", 1, (85000, 110000), "Hybrid", "L:Chief Operating Officer"),
    ("Marketing", "Content Writer", "Content", 4, (30000, 45000), "Hybrid", "L:Marketing Manager"),
    ("Marketing", "Social Media Executive", "Social Media", 3, (22000, 32000), "Hybrid", "L:Marketing Manager"),
    ("Marketing", "Marketing Analyst", "Marketing Analytics", 2, (40000, 55000), "Hybrid", "L:Marketing Manager"),
]
# Share of a department working from Maharashtra (Mumbai or Pune) rather than Bengaluru.
MAHARASHTRA_SHARE = {"Engineering": 0.4, "Client Services": 0.4, "Marketing": 0.3}
CITIES = {
    "Bengaluru, Karnataka": (["Indiranagar", "Koramangala", "HSR Layout", "Jayanagar", "Whitefield", "Malleshwaram",
                              "JP Nagar", "Banashankari", "Hebbal", "Rajajinagar"], "560"),
    "Mumbai, Maharashtra": (["Andheri West", "Bandra West", "Powai", "Goregaon East", "Dadar"], "400"),
    "Pune, Maharashtra": (["Kothrud", "Baner", "Viman Nagar", "Hinjewadi", "Aundh"], "411"),
}
QUALIFICATIONS = {
    "Leadership": ["MBA, IIM Bangalore", "B.Tech, IIT Madras"],
    "Design": ["B.Des, NID Ahmedabad", "B.Des, Srishti Manipal", "B.Des, MIT Institute of Design", "BFA, Sir J.J. School of Art"],
    "Engineering": ["B.Tech, NIT Surathkal", "B.E., RV College of Engineering", "B.E., PES University", "MCA, Pune University"],
    "Client Services": ["MBA, Christ University", "BBA, Symbiosis", "MBA, NMIMS"],
    "Studio Ops": ["B.Com, Bangalore University", "Diploma in Fine Arts", "BA, Mount Carmel College"],
    "Finance & Admin": ["CA", "M.Com, Bangalore University", "MBA (HR), Symbiosis"],
    "Marketing": ["BA English, St. Joseph's College", "MBA, Welingkar", "BMM, Xavier's Mumbai"],
}
# Five people join during the year, on Mondays, so they are paid for part of their first month.
JOINERS = ["2025-05-05", "2025-05-19", "2025-06-02", "2025-06-16", "2025-07-07"]

# ---------------------------------------------------------------- after July (written out, not drawn at random)
# What happens in August, September and October 2025, month by month, so Horilla can be "staged" one month at a time
# (scripts/horilla.sh stage august|september|october) and each month has people joining, people leaving and a raise
# approved late (arrears for a month already paid). Nothing here changes April-July.
NEW_PEOPLE = [
    {"code": "BL081", "name": "Kabir Sethi", "gender": "M", "location": "Bengaluru, Karnataka", "doj": "2025-08-04",
     "department": "Design", "designation": "Designer", "job_role": "Visual Designer", "work_type": "Hybrid", "manager": "BL011",
     "dob": "1999-03-17", "marital_status": "single", "children": 0, "qualification": "B.Des, Srishti Manipal", "experience": 3,
     "address": "Koramangala 5th Block", "pin": "560095", "phone": "9845012081", "gross": 44000},
    {"code": "BL082", "name": "Meghana Rao", "gender": "F", "location": "Mumbai, Maharashtra", "doj": "2025-08-18",
     "department": "Engineering", "designation": "Developer", "job_role": "Full-stack Developer", "work_type": "Hybrid",
     "manager": "BL029", "dob": "1997-11-02", "marital_status": "single", "children": 0, "qualification": "B.E. Computer Engineering, VJTI",
     "experience": 5, "address": "Powai", "pin": "400076", "phone": "9845012082", "gross": 72000},
    {"code": "BL083", "name": "Sara Thomas", "gender": "F", "location": "Bengaluru, Karnataka", "doj": "2025-09-01",
     "department": "Client Services", "designation": "Account Executive", "job_role": "Client Relationship",
     "work_type": "Work from office", "manager": "BL046", "dob": "2000-06-21", "marital_status": "single", "children": 0,
     "qualification": "BBA, Christ University", "experience": 2, "address": "HSR Layout", "pin": "560102", "phone": "9845012083",
     "gross": 30000},
    {"code": "BL084", "name": "Rohit Jadhav", "gender": "M", "location": "Pune, Maharashtra", "doj": "2025-09-22",
     "department": "Marketing", "designation": "Content Writer", "job_role": "Content", "work_type": "Hybrid", "manager": "BL071",
     "dob": "2002-01-09", "marital_status": "single", "children": 0, "qualification": "BA English, Fergusson College",
     "experience": 1, "address": "Kothrud", "pin": "411038", "phone": "9845012084", "gross": 20000},
    {"code": "BL085", "name": "Farah Kazi", "gender": "F", "location": "Mumbai, Maharashtra", "doj": "2025-10-06",
     "department": "Engineering", "designation": "QA Analyst", "job_role": "Quality Assurance", "work_type": "Hybrid",
     "manager": "BL029", "dob": "1996-04-28", "marital_status": "married", "children": 1, "qualification": "B.Sc. IT, Mumbai University",
     "experience": 6, "address": "Andheri East", "pin": "400069", "phone": "9845012085", "gross": 52000},
]
# Last working day (the person is paid up to and including it).
LEAVERS = {"BL033": "2025-08-14", "BL058": "2025-08-31", "BL042": "2025-09-19", "BL061": "2025-10-10"}
# Raises approved after the month they start in: paid as arrears in the month they are recorded.
LATE_RAISES = [
    ("BL064", "2025-07-01", 42500, "2025-08-11"),   # August pays July's difference
    ("BL042", "2025-08-01", 51500, "2025-09-09"),   # September pays August's difference; she leaves on 19 September
    ("BL037", "2025-07-01", 66000, "2025-09-10"),   # September pays July's and August's difference
    ("BL079", "2025-09-01", 45500, "2025-10-07"),   # October pays September's difference
]
# Attendance after July (scripts/make_brickline_spreadsheet.py writes it; Horilla's seed enters it): a check-in on every
# working day except approved leave and these. "absent": did not come and did not ask for leave (an unpaid absence the
# system should suggest). "forgot_punch": came in but has no check-in (suggested too; the maker says it was not one).
ATTENDANCE_GAPS = [
    {"code": "BL016", "date": "2025-08-12", "why": "absent"},
    {"code": "BL016", "date": "2025-08-13", "why": "absent"},
    {"code": "BL057", "date": "2025-08-21", "why": "forgot_punch"},
    {"code": "BL038", "date": "2025-09-08", "why": "absent"},
    {"code": "BL066", "date": "2025-09-26", "why": "absent"},
    {"code": "BL066", "date": "2025-09-29", "why": "absent"},
    {"code": "BL081", "date": "2025-10-14", "why": "absent"},
    {"code": "BL025", "date": "2025-10-23", "why": "forgot_punch"},
]
# A joiner whose attendance card was not set up in their first month: no check-in at all (not a month of absence).
NO_ATTENDANCE = [{"code": "BL084", "month": "2025-09"}]
# Company holidays (the same list Horilla gets).
COMPANY_HOLIDAYS = [
    ["Mahavir Jayanti", "2025-04-10"], ["Ambedkar Jayanti", "2025-04-14"], ["Good Friday", "2025-04-18"], ["May Day", "2025-05-01"],
    ["Bakrid", "2025-06-07"], ["Muharram", "2025-07-06"], ["Independence Day", "2025-08-15"], ["Ganesh Chaturthi", "2025-08-27"],
    ["Gandhi Jayanti", "2025-10-02"], ["Diwali", "2025-10-20"],
]
NEW_LEAVE = [
    {"code": "BL069", "type": "Casual Leave", "from": "2025-08-06", "to": "2025-08-06", "days": 1, "status": "Approved"},
    {"code": "BL033", "type": "Loss of Pay", "from": "2025-08-08", "to": "2025-08-08", "days": 1, "status": "Approved"},
    {"code": "BL024", "type": "Sick Leave", "from": "2025-08-19", "to": "2025-08-20", "days": 2, "status": "Approved"},
    {"code": "BL052", "type": "Loss of Pay", "from": "2025-08-25", "to": "2025-08-26", "days": 2, "status": "Rejected"},
    {"code": "BL075", "type": "Loss of Pay", "from": "2025-09-15", "to": "2025-09-16", "days": 2, "status": "Approved"},
    {"code": "BL082", "type": "Casual Leave", "from": "2025-09-24", "to": "2025-09-24", "days": 1, "status": "Approved"},
    {"code": "BL061", "type": "Earned Leave", "from": "2025-10-06", "to": "2025-10-08", "days": 3, "status": "Approved"},
    {"code": "BL081", "type": "Loss of Pay", "from": "2025-10-13", "to": "2025-10-13", "days": 1, "status": "Approved"},
    {"code": "BL050", "type": "Casual Leave", "from": "2025-10-27", "to": "2025-10-27", "days": 1, "status": "Requested"},
]


def gross_split(gross: int) -> dict:
    """The company's usual split: 50% basic, 20% HRA, the rest special allowance."""
    basic, hra = gross // 2, gross // 5
    return {"basic": basic, "hra": hra, "special": gross - basic - hra}


def working_days(start: date, end: date) -> int:
    return sum(1 for k in range((end - start).days + 1)
               if (start + timedelta(k)).weekday() < 5 and start + timedelta(k) not in HOLIDAYS)


def build():
    rng = random.Random(20250401)
    firsts = rng.sample(FEMALE, 37) + rng.sample(MALE, 37)
    rng.shuffle(firsts)
    gender_of = {n: "F" for n in FEMALE} | {n: "M" for n in MALE}

    employees, salaries, leave = list(CORE_EMPLOYEES), list(CORE_SALARIES), list(CORE_LEAVE)
    by_designation = {e["designation"]: [e["code"]] for e in CORE_EMPLOYEES}
    number = 7
    joiners = list(JOINERS)
    new_people = []
    for dept, designation, role, count, (lo, hi), work_type, reports in PLAN:
        for k in range(count):
            first = firsts.pop()
            code = f"BL{number:03d}"
            number += 1
            if reports and reports.startswith("L:"):
                bosses = by_designation[reports[2:]]
                manager = bosses[k % len(bosses)]
            else:
                manager = reports
            share = MAHARASHTRA_SHARE.get(dept, 0)
            location = ("Mumbai, Maharashtra" if rng.random() < 0.6 else "Pune, Maharashtra") if rng.random() < share else "Bengaluru, Karnataka"
            senior = designation.startswith(("Chief", "Design Lead", "Engineering Manager", "Account Director", "Finance Manager",
                                             "Marketing Manager", "Studio Manager"))
            if designation == "Design Intern":
                doj = date(2025, rng.randint(1, 3), rng.randint(1, 28))
            elif not senior and joiners and rng.random() < 0.12:
                doj = date.fromisoformat(joiners.pop(0))
            else:
                start, end = (date(2016, 1, 1), date(2019, 12, 31)) if senior else (date(2018, 1, 1), date(2025, 3, 15))
                doj = start + timedelta(rng.randint(0, (end - start).days))
            age = rng.randint(38, 50) if senior else rng.randint(22, 36)
            years = max(0, age - 22 - rng.randint(0, 2))
            area_list, pin_prefix = CITIES[location]
            person = {
                "code": code, "name": f"{first} {rng.choice(LAST)}", "gender": gender_of[first], "location": location,
                "doj": doj.isoformat(), "department": dept, "designation": designation, "job_role": role,
                "work_type": work_type, "manager": manager,
                "dob": date(2025 - age, rng.randint(1, 12), rng.randint(1, 28)).isoformat(),
                "marital_status": "married" if age > 30 and rng.random() < 0.7 else "single",
                "children": rng.choice([0, 1, 2]) if age > 32 else 0,
                "qualification": rng.choice(QUALIFICATIONS[dept]), "experience": years,
                "address": rng.choice(area_list), "pin": f"{pin_prefix}{rng.randint(1, 99):03d}", "phone": f"9845012{number - 1:03d}",
            }
            if person["marital_status"] == "single":
                person["children"] = 0
            employees.append(person)
            new_people.append(person)
            by_designation.setdefault(designation, []).append(code)

            gross = rng.randrange(lo, hi + 1, 500)
            starts = max(doj, date(2025, 4, 1))
            salaries.append({"code": code, "effective_from": starts.isoformat(), **gross_split(gross),
                             "recorded_on": (min(starts, date(2025, 3, 25)) if starts == date(2025, 4, 1) else starts - timedelta(days=7)).isoformat()})
    assert not joiners, f"joiners not placed: {joiners}"

    # Raises. On time: from 1 July, recorded in June. Late: effective May or June, recorded in July -> arrears in July.
    eligible = [p for p in new_people if p["doj"] < "2025-04-01" and p["designation"] != "Design Intern"]
    picked = rng.sample(eligible, 13)
    gross_of = {s["code"]: s["basic"] + s["hra"] + s["special"] for s in salaries}
    for i, p in enumerate(picked):
        late = i < 3
        effective = ["2025-05-01", "2025-06-01", "2025-06-01"][i] if late else "2025-07-01"
        recorded = ["2025-07-04", "2025-07-10", "2025-07-10"][i] if late else "2025-06-20"
        raised = int(round(gross_of[p["code"]] * (1 + rng.choice([0.08, 0.10, 0.12])) / 500.0)) * 500
        salaries.append({"code": p["code"], "effective_from": effective, **gross_split(raised), "recorded_on": recorded,
                         "note": "Raise approved late: recorded in July" if late else "Annual raise from July"})

    # Leave: single-week requests on working days, never overlapping for one person.
    taken = {}
    kinds = ["Casual Leave"] * 8 + ["Sick Leave"] * 6 + ["Earned Leave"] * 2 + ["Loss of Pay"] * 4
    while len(leave) < len(CORE_LEAVE) + 45:
        p = rng.choice(new_people)
        month = rng.randint(4, 7)
        first_day = date(2025, month, rng.randint(1, 26))
        if first_day.weekday() > 2 or first_day in HOLIDAYS or first_day.isoformat() < p["doj"]:
            continue  # starts Monday to Wednesday, after joining
        end = first_day + timedelta(rng.choice([0, 0, 0, 1, 1, 2]))
        span = {first_day + timedelta(k) for k in range((end - first_day).days + 1)}
        if span & taken.get(p["code"], set()) or any(d in HOLIDAYS for d in span) or end.month != month:
            continue
        taken.setdefault(p["code"], set()).update(span)
        roll = rng.random()
        status = "Requested" if month == 7 and roll < 0.12 else "Rejected" if roll > 0.9 else "Approved"
        leave.append({"code": p["code"], "type": rng.choice(kinds), "from": first_day.isoformat(), "to": end.isoformat(),
                      "days": working_days(first_day, end), "status": status})
    # After July: joiners, leavers, late raises and leave (see NEW_PEOPLE, LEAVERS, LATE_RAISES, NEW_LEAVE).
    for person in NEW_PEOPLE:
        person = dict(person)
        gross = person.pop("gross")
        employees.append(person)
        salaries.append({"code": person["code"], "effective_from": person["doj"], **gross_split(gross),
                         "recorded_on": (date.fromisoformat(person["doj"]) - timedelta(days=7)).isoformat()})
    for person in employees:
        if person["code"] in LEAVERS:
            person["doe"] = LEAVERS[person["code"]]
    for code, effective, gross, recorded in LATE_RAISES:
        month = date.fromisoformat(recorded).strftime("%B")
        salaries.append({"code": code, "effective_from": effective, **gross_split(gross), "recorded_on": recorded,
                         "note": f"Raise approved late: recorded in {month}"})
    leave += NEW_LEAVE

    leave.sort(key=lambda r: (r["from"], r["code"]))
    salaries.sort(key=lambda r: (r["code"], r["effective_from"]))

    return {
        "_comment": "Made-up data for Brickline Design Co. (written by scripts/make_brickline_data.py; the first six people are "
                    "the ones the answer sheets check). Single source for the spreadsheet, the Demo HRMS and Horilla's seed.",
        "company": COMPANY, "employees": employees, "salaries": salaries, "leave": leave,
        "holidays": COMPANY_HOLIDAYS, "attendance_gaps": ATTENDANCE_GAPS, "no_attendance": NO_ATTENDANCE,
    }


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    late = sum(1 for s in data["salaries"] if s.get("note", "").startswith("Raise approved late"))
    leavers = sum(1 for e in data["employees"] if e.get("doe"))
    print(f"wrote {OUT}: {len(data['employees'])} people ({leavers} leaving), {len(data['salaries'])} salary records "
          f"({late} raises recorded late), {len(data['leave'])} leave requests")
