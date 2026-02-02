
python3 export_vivaldi_history.py --weeks 20 --browser vivaldi --output-dir timeline_data

python3 export_vivaldi_history.py --weeks 20 --browser chrome --output-dir timeline_data

python3 merge_timeline_data.py 

python3 plot_timeline_data.py 