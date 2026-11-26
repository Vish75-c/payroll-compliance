// What each screen is for and how it works. The screens show data and actions only; this text opens from the "?"
// next to a page's title (and once, the first time a person opens that page), and all of it is on the Help page.

export const HELP = {
  overview: {
    title: "Overview",
    intro: "Where the company stands: the latest month, what is waiting, and what has been paid.",
    points: [
      "The monthly work is two steps: bring in the HR data, then run the payroll month.",
      "Totals compare with the month before. Click a month to open it.",
      "A month is final once the finance head approves it; its numbers never change after that.",
    ],
    seeAlso: ["months", "hr_data"],
  },
  hr_data: {
    title: "HR data",
    intro: "Employees, salaries and leave, read from the HR tool's screens or from its exports.",
    points: [
      "Upload files: .xlsx or .csv, any column names. Add every list at once (employees, salaries, leave, attendance).",
      "Or connect the HR tool: the agent signs in, finds the lists behind menus and tabs, and reads them.",
      "You confirm what each column means once; later reads of the same list reuse it without asking the AI.",
      "Nothing is used for pay until a finance team employee marks the data as reviewed.",
      "A known HR tool (e.g. Horilla) is recognised from its columns, so its exports map without asking.",
    ],
    seeAlso: ["hr_job", "hr_snapshot"],
  },
  hr_job: {
    title: "Reading and mapping the data",
    intro: "Each list read is shown with what every column was understood to be. Check it, then confirm.",
    points: [
      "Columns marked 'ignore' are not used. Change a column's meaning from its drop-down.",
      "Leave: say which leave types cut pay (unpaid) and which statuses count (approved).",
      "Half days: say which values are a half day (Horilla writes 'First Half' / 'Second Half'); a half day counts 0.5.",
      "Missing a list? Add the file before building the data, e.g. the leave file that came after the salary file.",
      "Not happy with a suggestion? Ask again: the rule-based matcher or the AI suggests once more.",
    ],
    seeAlso: ["hr_snapshot"],
  },
  hr_snapshot: {
    title: "Checking the data",
    intro: "The data as it will be used for pay: every employee, salary, leave and attendance row, with checks.",
    points: [
      "Errors must be fixed in the HR tool or the file before the data can be used.",
      "Warnings often change pay: leave with no type counts as unpaid, a missing joining date pays a full month. Read them before marking the data as reviewed.",
      "Unpaid days are shown per month. A leave across two months is split by its dates; a half day stays on its own date.",
      "Attendance: working days with no check-in and no approved leave are only suggested on the month page; nothing is cut until a person decides.",
    ],
    seeAlso: ["run.data"],
  },
  months: {
    title: "Payroll months",
    intro: "Open a month, then prepare, calculate and approve it.",
    points: [
      "Open the next month with the latest reviewed HR data. Each month keeps the data it was calculated with.",
      "Days for one day's pay: the company setting (days in the month, 30 or 26); a month can set its own when it is opened.",
      "Salary changes known as of: leave empty for today. A past date re-creates a month as it was known then.",
      "With pay groups, each group's dates are shown for the month you pick.",
    ],
    seeAlso: ["run.data", "pay_groups"],
  },
  "run.data": {
    title: "Step 1 · Review HR data",
    intro: "Check the HR data this month uses, then mark it as reviewed.",
    points: [
      "Salary changes recorded up to the knowledge date are used. A raise recorded later is paid as arrears.",
      "If newer HR data arrives, the month says so; use it before approving.",
      "One day's pay is the monthly pay divided by the day count shown; change it for this month only if needed.",
    ],
    seeAlso: ["hr_snapshot"],
  },
  "run.changes": {
    title: "Step 2 · Changes",
    intro: "Anything this month needs that the HR data does not have: a bonus, a deduction, an unpaid absence, a raise.",
    points: [
      "Add by hand with the form, or type (or speak) a sentence such as 'Give Priya a ₹10,000 bonus this month'.",
      "A sentence is read by the AI or the rule-based reader. Amounts must be typed: they are never worked out by AI.",
      "Every change is confirmed by a person before it counts, and stays in the month's history.",
      "Possible absences: working days with no check-in and no approved leave. Add each as unpaid, or say why it is not an absence.",
      "An unpaid absence found after its month was paid is recovered in this month, worked out with that month's rules.",
    ],
    seeAlso: ["run.calculate"],
  },
  "run.calculate": {
    title: "Step 3 · Calculate",
    intro: "Every employee's pay, worked out from the data, the changes and the rules. Click any number to see why.",
    points: [
      "'What changed' compares with the month before; 'Paid differently' lists the people whose pay moved.",
      "In plain words: an AI writes a summary from the comparison. It changes nothing, and every number in it is checked against the calculation.",
      "With pay groups, people are listed by group with subtotals. A 'transition' chip means the person's pay cycle changed: the period runs from the day after their last paid day and is paid for the days it covers.",
      "What if?: a draft re-run with other settings, to compare. The month itself does not change.",
      "Same inputs, same result: calculate again from the stored inputs (no database, no AI) and compare fingerprints.",
    ],
    seeAlso: ["run.submit", "pay_groups"],
  },
  "run.submit": {
    title: "Step 4 · Submit",
    intro: "Send the calculated month to the finance head for approval.",
    points: [
      "A month can be submitted once it is calculated and no change is waiting to be confirmed.",
      "If anything moved after calculating (data, a change, a rule), calculate again first.",
      "Sent back? The finance head's reason is shown. Make the corrections, calculate and submit again.",
    ],
    seeAlso: ["run.approve"],
  },
  "run.approve": {
    title: "Step 5 · Finance head approves",
    intro: "The finance head checks what changed and approves, or sends the month back with a reason.",
    points: [
      "Four eyes: the person who prepared the month cannot approve it.",
      "The approval is for the exact result submitted; its fingerprint is shown.",
      "One approval covers every pay group; each group is still paid on its own day.",
    ],
    seeAlso: ["run.files"],
  },
  "run.files": {
    title: "Step 6 · Register and files",
    intro: "The register and the payment files, written when the month is approved, and what to pay the government.",
    points: [
      "Files: the register and one file each for PF, ESI, professional tax and TDS; with pay groups, a net pay file per group for the bank.",
      "Every file is read back and checked against the register before the month is final.",
      "One row per employee. No government IDs are in the files, and no payment is made by the system.",
      "Government dues: the deductions plus the company's own PF and ESI. Record each payment with its challan number.",
    ],
    seeAlso: ["dues"],
  },
  employees: {
    title: "Employees",
    intro: "Each person's pay group for a month, and where it comes from.",
    points: [
      "Order used for each person: set by hand → the 'Pay group' column in the HR data → the first group whose rule covers their department, state or designation → the default group.",
      "To move people: tick them, pick the group and the month it starts from, and press Move. The HR data never replaces a hand setting.",
      "Clear hand setting: the person goes back to the group the HR data gives.",
      "Moving someone to a group on another cycle makes their first month there a transition period, paid for the days it covers.",
      "Only a finance team employee can move people; months already final cannot be changed.",
    ],
    seeAlso: ["pay_groups"],
  },
  pay_groups: {
    title: "Pay groups",
    intro: "People paid on the same cycle and the same day, e.g. office staff on the calendar month paid on the 1st, factory staff on the 26th-to-25th paid on the 28th.",
    points: [
      "Calendar month: salary and unpaid days for the 1st to the last day.",
      "Unpaid days on a cycle: salary for the calendar month; unpaid days counted from the start day (leave after the cutoff counts next month).",
      "Shifted: salary and unpaid days both counted from the start day (with 26: November is 26 Oct to 25 Nov).",
      "Paid on: the first such day after the cycle ends. TDS is deducted on that day, so it is due by the 7th of the month after.",
      "Covers: departments, work states or designations the group takes. A 'Pay group' column in the HR data or a hand setting comes first.",
      "A change starts from a month that is not final. Its first month is a transition period, so no day is paid twice or missed.",
      "Everyone else (the default group) takes everyone the others do not; its pay day is the company's 'salary paid on' day.",
    ],
    seeAlso: ["employees", "rules"],
  },
  rules: {
    title: "Rules & settings",
    intro: "Government rules and company settings. Each is dated: a change is a new version from a start date.",
    points: [
      "Rules are read as of the 1st of each month, so a version that starts mid-month first applies to the next month.",
      "A version used by a submitted or final month cannot be removed (🔒 used); add a newer version instead.",
      "Months already final are not changed by a new version; a draft re-run shows what they would be.",
      "Pay groups set who is paid on which cycle and day.",
    ],
    seeAlso: ["pay_groups"],
  },
  dues: {
    title: "Government dues",
    intro: "What the company must deposit with the government after each final month, by when, and whether it has.",
    points: [
      "Amounts come from the final month and never change. Due dates come from the rule the month used.",
      "TDS is due by the 7th of the month after salary is paid (30 April for March). With pay groups paid in different months, TDS is one line per month.",
      "Record each payment with its date and challan / TRRN / CRN number; an undo needs a reason. Both go into the month's history.",
      "Only the finance head sees and records government dues.",
    ],
    seeAlso: ["run.files"],
  },
  ai: {
    title: "AI log",
    intro: "Every question sent to an AI model, what it answered, and who checked it.",
    points: [
      "The AI only suggests: column meanings, changes read from a sentence, a summary in plain words. A person confirms each one.",
      "Amounts are never worked out by AI; they must be typed.",
      "With AI off for the company, nothing leaves the servers: known HR tool profiles, rules and people map the data.",
    ],
  },
  "onboarding.company": {
    title: "Add a company · Company details",
    intro: "Name, state, registration numbers and the first payroll month on this system.",
    points: [
      "First payroll month: if it is not April, you will enter pay and TDS from earlier in the year (opening balances).",
      "PAN and TAN are needed to deposit TDS; PF and ESI codes appear with the dues and on the files.",
      "Salary paid on: the day of the month salaries are paid. It sets when TDS is due. By law, within 7 days after the month ends (10 with 1,000+ workers).",
      "You can leave at any step and come back; each step is saved as you go.",
    ],
    seeAlso: ["onboarding.settings"],
  },
  "onboarding.settings": {
    title: "Add a company · Pay settings",
    intro: "Saved as dated company settings from the start of the financial year; all can be changed later on Rules & settings.",
    points: [
      "Pay cycle: the cycle for everyone here (the default pay group). People on another cycle go in their own pay group later.",
      "One day's pay: monthly pay ÷ the days in the month, or a fixed number (26 working days and 30 are common). Used for unpaid days and days before joining.",
      "Salary split: a salary given as one amount is split into basic, HRA and special allowance (the rest).",
      "Government rules: the Labour Codes 50% wage rule (from 21 Nov 2025), or the older laws if you have been advised the Codes do not cover the company yet.",
      "AI off: only known HR tool profiles, rules and people map the data; nothing leaves the servers.",
    ],
    seeAlso: ["pay_groups", "rules"],
  },
  "onboarding.hr_data": {
    title: "Add a company · HR data",
    intro: "Employees, salaries and leave, from the HR tool's screens or its exports.",
    points: ["You confirm what each column means once; later reads reuse it."],
    seeAlso: ["hr_data", "hr_job"],
  },
  "onboarding.data_checked": {
    title: "Add a company · Check the data",
    intro: "Read the checks and mark the data as right.",
    points: [
      "Warnings often change pay (leave with no type counts as unpaid, a missing joining date pays a full month), so read them first.",
      "Open the data to see every employee, salary and leave row.",
    ],
    seeAlso: ["hr_snapshot"],
  },
  "onboarding.opening_balances": {
    title: "Add a company · Opening balances",
    intro: "Pay and TDS from earlier in the financial year, paid by the old payroll.",
    points: [
      "Enter each person's totals so far; TDS for the rest of the year is spread from them.",
      "Columns: Employee ID, Gross paid this year, TDS this year. A template is available to download.",
      "Not needed when the first month is April.",
    ],
  },
  "onboarding.first_month": {
    title: "Add a company · First month",
    intro: "Open the first payroll month and calculate it.",
    points: ["From here each month works like any other: review the data, add changes, calculate, submit, approve."],
    seeAlso: ["months"],
  },
};

export const HELP_ORDER = [
  "overview", "hr_data", "hr_job", "hr_snapshot", "months", "run.data", "run.changes", "run.calculate", "run.submit", "run.approve",
  "run.files", "employees", "pay_groups", "rules", "dues", "ai", "onboarding.company", "onboarding.settings", "onboarding.hr_data",
  "onboarding.data_checked", "onboarding.opening_balances", "onboarding.first_month",
];
