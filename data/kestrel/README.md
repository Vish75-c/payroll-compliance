# Kestrel Analytics: sample data for pay cycles and pay groups

Made-up company. Made by `scripts/make_kestrel_data.py`, which also works out the expected November 2026 below
with the calculation itself.

## Set it up in the app

1. **Add a company** with these details: Kestrel Analytics Pvt Ltd · Karnataka · Bengaluru · PAN AABCK5678L ·
   TAN BLRK05678C · PF office KNBNG, number 0056789, extension 000 · ESI 53000567890001001 · salary paid on day **1** ·
   first payroll month **November 2026**.
2. **Pay settings:** leave the pay cycle as **Calendar month** (that is the default group, "Everyone else").
3. **Rules & settings → Pay groups → + New pay group**, twice:
   * **Management**: cycle **Calendar month**, paid on the **1st**, covers department **Leadership**.
   * **Shift staff**: cycle **Shifted**, starts on day **26**, paid on the **28th**, covers departments
     **Analytics** and **Engineering**.
4. **HR data:** upload `employees.xlsx`, `salaries.xlsx` and `leave.csv` together, confirm the columns (the leave file
   has Horilla's *Start Date Breakdown* / *End Date Breakdown*: First Half is a half day), mark the data as right.
5. **Employees:** KS001 is in *Management* (rule: Department Leadership), KS002–KS004 in *Shift staff* (rule:
   Department), and KS005 in *Shift staff* from the HR file's own **Pay Group** column ("HR data"). To show a hand
   setting, tick someone and use *Move to pay group* from a month that is not final.
6. **Opening balances:** upload `opening_balances.csv` (April to October from the old payroll).
7. **First month:** open November 2026 and calculate.

To see one cycle for everyone instead, skip step 3 and pick that cycle in step 2 (a second company: a cycle change
can only start from a month that is not final yet).

## What each person tests

| Code | Name | Department | Joined | Monthly pay | Case |
|---|---|---|---|---|---|
| KS001 | Arvind Rao | Leadership | 15 Apr 2019 | ₹2,50,000 | TDS every month of the year |
| KS002 | Meera Joshi | Analytics | 5 Jul 2021 | ₹90,000 → ₹1,40,000 from 1 Nov | raise from 1 Nov: TDS starts this month |
| KS003 | Sameer Kulkarni | Analytics | 14 Feb 2022 | ₹45,000 | unpaid leave before and inside the cycle |
| KS004 | Nikhil Verma | Engineering | 28 Oct 2026 | ₹60,000 | new joiner, inside November's cycle but before 1 Nov |
| KS005 | Anjali Desai | Analytics | 17 Nov 2026 | ₹42,000 | new joiner this month |

Unpaid leave (Loss of Pay, approved): KS003 on **21–22 Oct** (before November's cycle, already cut by the old
payroll in October) and **27–28 Oct** (inside November's cycle); KS004 **half a day on 10 Nov** (First Half).

Opening balances (April to October): KS001 ₹17,50,000 paid, ₹2,77,550 TDS; KS002 ₹6,30,000 paid, – TDS; KS003 ₹3,12,000 paid, – TDS.

## Expected November 2026: the pay groups (the setup above)

| Code | Pay group | Paid for | Unpaid days counted | Pay date | Days | Not employed | Unpaid | Salary for the period | Unpaid cut | Gross | PF | ESI | PT | TDS | Net |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KS001 | Management | 1 Nov to 30 Nov 2026 | 1 Nov to 30 Nov 2026 | 1 Dec | 30 | 0 | 0 | ₹2,50,000 | – | ₹2,50,000 | ₹1,800 | – | ₹200 | ₹39,650 | **₹2,08,350** |
| KS002 | Shift staff | 26 Oct to 25 Nov 2026 | 26 Oct to 25 Nov 2026 | 28 Nov | 31 | 0 | 0 | ₹1,30,323 | – | ₹1,30,323 | ₹1,800 | – | ₹200 | ₹9,427 | **₹1,18,896** |
| KS003 | Shift staff | 26 Oct to 25 Nov 2026 | 26 Oct to 25 Nov 2026 | 28 Nov | 31 | 0 | 2 | ₹45,000 | ₹2,904 | ₹42,096 | ₹1,800 | – | ₹200 | – | **₹40,096** |
| KS004 | Shift staff | 26 Oct to 25 Nov 2026 | 26 Oct to 25 Nov 2026 | 28 Nov | 31 | 2 | 0.5 | ₹60,000 | ₹4,839 | ₹55,161 | ₹1,800 | – | ₹200 | – | **₹53,161** |
| KS005 | Shift staff | 26 Oct to 25 Nov 2026 | 26 Oct to 25 Nov 2026 | 28 Nov | 31 | 22 | 0 | ₹42,000 | ₹29,806 | ₹12,194 | ₹732 | ₹92 | – | – | **₹11,370** |

TDS to deposit: ₹9,427 by 7 Dec 2026 (deducted Nov 2026); ₹39,650 by 7 Jan 2027 (deducted Dec 2026). PF and ESI by 15 Dec 2026.

## Expected November 2026: everyone on the shifted cycle, day 26

| Code | Pay group | Paid for | Unpaid days counted | Pay date | Days | Not employed | Unpaid | Salary for the period | Unpaid cut | Gross | PF | ESI | PT | TDS | Net |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KS001 | Everyone else | 26 Oct to 25 Nov 2026 | 26 Oct to 25 Nov 2026 | 1 Dec | 31 | 0 | 0 | ₹2,50,000 | – | ₹2,50,000 | ₹1,800 | – | ₹200 | ₹39,650 | **₹2,08,350** |
| KS002 | Everyone else | 26 Oct to 25 Nov 2026 | 26 Oct to 25 Nov 2026 | 1 Dec | 31 | 0 | 0 | ₹1,30,323 | – | ₹1,30,323 | ₹1,800 | – | ₹200 | ₹9,427 | **₹1,18,896** |
| KS003 | Everyone else | 26 Oct to 25 Nov 2026 | 26 Oct to 25 Nov 2026 | 1 Dec | 31 | 0 | 2 | ₹45,000 | ₹2,904 | ₹42,096 | ₹1,800 | – | ₹200 | – | **₹40,096** |
| KS004 | Everyone else | 26 Oct to 25 Nov 2026 | 26 Oct to 25 Nov 2026 | 1 Dec | 31 | 2 | 0.5 | ₹60,000 | ₹4,839 | ₹55,161 | ₹1,800 | – | ₹200 | – | **₹53,161** |
| KS005 | Everyone else | 26 Oct to 25 Nov 2026 | 26 Oct to 25 Nov 2026 | 1 Dec | 31 | 22 | 0 | ₹42,000 | ₹29,806 | ₹12,194 | ₹732 | ₹92 | – | – | **₹11,370** |

TDS to deposit: ₹49,077 by 7 Jan 2027. PF and ESI by 15 Dec 2026.

## Expected November 2026: everyone on the attendance cutoff, day 26

| Code | Pay group | Paid for | Unpaid days counted | Pay date | Days | Not employed | Unpaid | Salary for the period | Unpaid cut | Gross | PF | ESI | PT | TDS | Net |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KS001 | Everyone else | 1 Nov to 30 Nov 2026 | 26 Oct to 25 Nov 2026 | 1 Dec | 30 | 0 | 0 | ₹2,50,000 | – | ₹2,50,000 | ₹1,800 | – | ₹200 | ₹39,650 | **₹2,08,350** |
| KS002 | Everyone else | 1 Nov to 30 Nov 2026 | 26 Oct to 25 Nov 2026 | 1 Dec | 30 | 0 | 0 | ₹1,40,000 | – | ₹1,40,000 | ₹1,800 | – | ₹200 | ₹11,440 | **₹1,26,560** |
| KS003 | Everyone else | 1 Nov to 30 Nov 2026 | 26 Oct to 25 Nov 2026 | 1 Dec | 30 | 0 | 2 | ₹45,000 | ₹3,000 | ₹42,000 | ₹1,800 | – | ₹200 | – | **₹40,000** |
| KS004 | Everyone else | 1 Nov to 30 Nov 2026 | 26 Oct to 25 Nov 2026 | 1 Dec | 30 | 0 | 0.5 | ₹60,000 | ₹1,000 | ₹59,000 | ₹1,800 | – | ₹200 | – | **₹57,000** |
| KS005 | Everyone else | 1 Nov to 30 Nov 2026 | 26 Oct to 25 Nov 2026 | 1 Dec | 30 | 16 | 0 | ₹42,000 | ₹22,400 | ₹19,600 | ₹1,176 | ₹147 | – | – | **₹18,277** |

TDS to deposit: ₹51,090 by 7 Jan 2027. PF and ESI by 15 Dec 2026.

## Expected November 2026: everyone on the calendar month

| Code | Pay group | Paid for | Unpaid days counted | Pay date | Days | Not employed | Unpaid | Salary for the period | Unpaid cut | Gross | PF | ESI | PT | TDS | Net |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KS001 | Everyone else | 1 Nov to 30 Nov 2026 | 1 Nov to 30 Nov 2026 | 1 Dec | 30 | 0 | 0 | ₹2,50,000 | – | ₹2,50,000 | ₹1,800 | – | ₹200 | ₹39,650 | **₹2,08,350** |
| KS002 | Everyone else | 1 Nov to 30 Nov 2026 | 1 Nov to 30 Nov 2026 | 1 Dec | 30 | 0 | 0 | ₹1,40,000 | – | ₹1,40,000 | ₹1,800 | – | ₹200 | ₹11,440 | **₹1,26,560** |
| KS003 | Everyone else | 1 Nov to 30 Nov 2026 | 1 Nov to 30 Nov 2026 | 1 Dec | 30 | 0 | 0 | ₹45,000 | – | ₹45,000 | ₹1,800 | – | ₹200 | – | **₹43,000** |
| KS004 | Everyone else | 1 Nov to 30 Nov 2026 | 1 Nov to 30 Nov 2026 | 1 Dec | 30 | 0 | 0.5 | ₹60,000 | ₹1,000 | ₹59,000 | ₹1,800 | – | ₹200 | – | **₹57,000** |
| KS005 | Everyone else | 1 Nov to 30 Nov 2026 | 1 Nov to 30 Nov 2026 | 1 Dec | 30 | 16 | 0 | ₹42,000 | ₹22,400 | ₹19,600 | ₹1,176 | ₹147 | – | – | **₹18,277** |

TDS to deposit: ₹51,090 by 7 Jan 2027. PF and ESI by 15 Dec 2026.

## What to look for

* **Groups in one month:** the register lists *Management* (1–30 Nov, paid 1 Dec) and *Shift staff*
  (26 Oct – 25 Nov, paid 28 Nov), each with a subtotal, and there is a net pay file per group.
* **TDS split by pay date:** Shift staff are paid on 28 Nov, so their TDS is deducted in November (due 7 Dec);
  KS001 is paid on 1 Dec (due 7 Jan).
* **KS003:** the shifted cycle cuts the 2 days of 27–28 Oct; the calendar month cuts nothing in November.
  21–22 Oct is never in November.
* **KS004** (joined 28 Oct): shifted pays from 28 Oct, so 2 days (26–27 Oct) are not employed; and **0.5** unpaid
  day for the half day on 10 Nov (Horilla's First Half).
* **KS005** (joined 17 Nov): shifted pays 17–25 Nov (9 of 31 days); the calendar month 17–30 Nov (14 of 30 days).
* **KS002:** TDS starts this month: the raise takes the year's pay over ₹12 lakh after the standard deduction.
* **KS001:** TDS as in every month, adjusted for what was deducted from April to October.
* **KS005** pays ESI this month: with the default setting, ESI cover is decided on this month's gross, which is
  under ₹21,000 because she joined late. Change it on Rules & settings (ESI wage) to decide on the monthly salary instead.
* **Changing a cycle later:** a change from December makes December a transition period for the people it moves
  (from the day after their last paid day to the end of the new cycle, paid for the days it covers).
