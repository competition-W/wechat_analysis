# wechat_analysis

source .venv/bin/activate
uvicorn api.main:app --host 0.0.0.0 --port 8000



python3 tests/test_system_performance.py 


python tests/test_system_performance.py \
  --max-concurrent 10 \
  --types sentiment sensitive summary highfreq unanswered \
  --save-report \
  --generate-doc \
  --generate-table \
  --generate-html

  cd /mnt/ai/omicshub/wechat_analysis && source .venv/bin/activate && python tests/test_system_performance.py --max-concurrent 10 --types sentiment sensitive summary highfreq unanswered --save-report --generate-doc --generate-table --generate-html --generate-excel