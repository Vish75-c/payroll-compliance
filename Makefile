.PHONY: start stop restart test answers demo-history
start:        ## run everything locally
	./scripts/start.sh
stop:
	./scripts/stop.sh
restart:      ## restart every service; waiting months keep waiting
	./scripts/stop.sh && ./scripts/start.sh
test:         ## all tests, including the answer sheets
	cd backend && ../.venv/bin/python -m pytest
answers:      ## answer-sheet report (add answer_sheets/case_C/ for case C)
	cd backend && ../.venv/bin/python manage.py check_answer_sheet
demo-history: ## process April-June as already paid
	cd backend && ../.venv/bin/python manage.py run_history
