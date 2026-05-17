.PHONY: eval test

eval:
	python -m evals.run_evals

test:
	python -B -m pytest backend/tests evals -p no:cacheprovider
	cd frontend && npm test
	cd frontend && npm run build
