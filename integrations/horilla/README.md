# Horilla

Horilla is one HR tool the agent reads. Nothing in the payroll system is specific to Horilla: the same
agent reads the bundled Demo HRMS, which names and lays out everything differently.

## Run Horilla with Brickline's data

Without Docker (tested, Nov 2026 Horilla, Python 3.12, SQLite):

```bash
./scripts/horilla.sh setup     # clone, install, admin/admin, enter Brickline data as it stood in June
./scripts/horilla.sh start     # http://localhost:8000
```

With Horilla's own Docker stack (`git clone https://github.com/horilla-opensource/horilla && cd horilla && docker compose up`):

```bash
docker compose exec web python manage.py createsuperuser --username admin --email admin@brickline.example
BRICKLINE_STAGE=june docker compose exec -T -e BRICKLINE_STAGE web python manage.py shell < /path/to/integrations/horilla/seed_brickline.py
```

## The July story

1. With Horilla at the June stage, read it from the payroll app and process April-June (`make demo-history`
   or open each month in the app).
2. HR enters Priya's raise in July: `./scripts/horilla.sh stage july` (adds a contract starting 1 May 2025).
3. Read Horilla again in the app. The field mapping confirmed the first time is reused without the AI.
   The raise is new, so it is recorded as first seen today: April-June stay as paid, and July pays arrears.

## After July: people joining, people leaving, raises approved late

`./scripts/horilla.sh stage august`, then `september`, then `october`: one stage per payroll month, run after the
previous month is paid in the app. Each enters what HR knew by the end of that month.

| Stage | Joining | Leaving (last working day) | Raise approved late (arrears) |
|---|---|---|---|
| august | Kabir Sethi (BL081) 4 Aug; Meghana Rao (BL082, Mumbai) 18 Aug | Gaurav Dutta (BL033) 14 Aug, with 1 day unpaid leave on 8 Aug; Shalini Shenoy (BL058) 31 Aug | Deepak Krishnan (BL064) ₹38,500 → ₹42,500 from 1 July, approved 11 Aug: August pays July's ₹4,000 |
| september | Sara Thomas (BL083) 1 Sep; Rohit Jadhav (BL084, Pune) 22 Sep | Jyoti Pillai (BL042) 19 Sep | Zoya Sen (BL037) ₹60,000 → ₹66,000 from 1 July, approved 10 Sep: July and August; Jyoti Pillai ₹47,000 → ₹51,500 from 1 Aug, approved 9 Sep: August, then her last part-month |
| october | Farah Kazi (BL085, Mumbai) 6 Oct | Mohit Thomas (BL061) 10 Oct | Mansi Dutta (BL079) ₹41,500 → ₹45,500 from 1 Sep, approved 7 Oct: September |

A person leaving gets Horilla's **End Date** ("Contract End Date" on the work information) = the last working day,
and stays active until the final settlement: an archived employee no longer appears on Horilla's list. The
profile reads End Date as the date of leaving, so that month is paid up to that day and later months leave them
out. Because Horilla keeps no "recorded on" date, a raise shows as arrears only if it appears in Horilla after
the month it starts in was paid: stage the months in order. The stage reached is written to `var/horilla/stage`.

## Attendance

Horilla keeps a check-in per person per day: **Attendance → Attendances**, tab **Validated Attendances** (the page
opens on *Attendance To Validate*, which is empty because every seeded day is already validated), and the matching
clock-in/clock-out entries under **Attendance → Attendance Activities** (Check-in / Check-out Log). Export a month from **Attendance → Attendances → Export** with the
date filter set to that month (the default columns are fine) and upload it in the payroll app's *Attendance* box
with the HR data. The profile maps it without AI (sample: `fixtures/Attendance_export_2025-08.xlsx`). Working days
with no check-in and no approved leave are then suggested on the month page; a maker adds or dismisses each one.
The seed leaves out check-ins on the days in `attendance_gaps` of `seed.json` (two real absences and a forgotten
punch each month from August) and for a September joiner with no attendance card yet.

## How Brickline sits in Horilla

| Brickline | Horilla screen | Columns the agent maps |
|---|---|---|
| Employees | Employees (`/employee/employee-view/`), plus each employee's own page | `Employee` holds "Priya Sharma (BL001)"; the name and ID are split. `Joining Date`, `Gender` and `Work Location` come from the employee's page |
| Salary | Contracts (`/payroll/view-contract/?contract_status=...`) | `Start Date` → salary effective from; `Contract wage` → monthly salary (split 50/20/30 by the company's salary structure setting) |
| Leave | Leave Requests (`/leave/request-view/`) | `Leave Type`, `Start Date`, `End Date`, `Requested Days`, `Status` |

The people come from `data/brickline/seed.json` (written by `scripts/make_brickline_data.py`), the same 80
people the spreadsheet and the Demo HRMS hold: 67 in Bengaluru, 13 in Mumbai and Pune, five who join during the
year, July raises (four of them recorded late, so July pays arrears) and 50 leave requests. The first six
(Priya, Arjun, Meera, Kavya, Ravi, Sneha) are the ones the answer sheets check.

So Horilla looks like a real company's, the seed also enters, for display only (pay does not use them):

* **Organisation:** seven departments (Leadership, Design, Engineering, Client Services, Studio Ops,
  Finance & Admin, Marketing), a job position and job role per person, reporting managers, employee type
  Full-time, and work types (Work from office / Hybrid / Remote). It also adds personal details: date of
  birth, qualification, address and phone.
* **Shift and holidays:** a Mon-Fri "General (09:30-18:30)" shift with an 8h30 minimum, and Karnataka 2025
  holidays (Mahavir Jayanti, Ambedkar Jayanti, Good Friday, May Day, ...).
* **Attendance:** one validated check-in/check-out per working day from 1 April (or the joining date) to the
  end of the stage's month (June or July), about 6,500 records by July. Weekends, holidays and approved leave are skipped, so attendance agrees with
  the leave the payroll system reads. Sneha's rejected leave day shows as a normal working day. Times are
  random but the same on every run. Overtime counts only beyond one extra hour, so no day waits for
  overtime approval.

## The Horilla profile

What we know about Horilla is written down in `profile.json` (see GUIDE.md 6.1):

* its screens, including the contract list address with every status;
* its list screens and its Excel exports (Employees work info, Contracts, Leave Requests), with what each column
  means.

`fixtures/` holds real exports made from the seeded data, with every contract status and Leave Type ticked.
Uploading them, or reading the screens, needs no AI. The mapping is pre-filled and the maker confirms it once.

Quirks the profile handles:

* **Contracts export:** calls the contract wage "Basic Salary". It is the monthly wage and is mapped to monthly
  gross (the screen calls it "Contract wage").
* **Employees export:** has a "Basic Salary" that is 0. It is not used; pay comes from contracts.
* **Leave Requests export:** leaves "Leave Type" unticked by default. The profile asks for it, otherwise every
  leave would count as unpaid.

Things learned from the real screens:

* Horilla only lets a user sign in when an Employee is linked to it; the seed links `admin` to Kavya Reddy.
* Horilla creates an empty "<name>'s Contract" (wage 0) for every new employee; the seed removes them.
* The Contracts list shows only active and draft contracts unless asked. Arrears need the old contract,
  so the screen address asks for every status. If an old salary is still missing, the payroll system
  does not guess: it warns that arrears for that month could not be checked.
* Lists show 20 rows a page. The page arrows are icons with no text, and the page-number box says how many
  pages there are (`max`). The agent recognises arrow icons inside the page controls, waits for the new rows
  (Horilla swaps them in a moment after the click), and reports an error if it reads fewer pages than the box says.
* A fresh sign-in starts a guided product tour whose overlay covers the page. The agent closes it (Escape, or
  the tour's Close/Skip button) before clicking, as a person would.
* The Employees list has no joining date, gender or work location. Rows open the employee's page when clicked
  (`onclick` sets the address), so the agent opens each one and reads those fields there. Without them, someone
  who joins mid-month would be paid for the whole month and Maharashtra staff would get Karnataka's
  professional tax; the data snapshot now warns when they are missing, whatever the source.
* The list screens show no "of N" row total, so completeness is also checked by other means (the page count,
  every employee has a salary, every leave row belongs to a known employee) and the maker reviews the counts.

Result of the full run against Horilla (80 people, at the July stage): all 41 July numbers of answer sheet A match, and every one
of the 2,561 July amounts is the same as when the data comes from the spreadsheet.
