"""저장소 루트에서 python -m estate <명령>으로 실행합니다."""
import argparse
import runpy
import sys
COMMANDS = {
    'serve':'estate.site.serve',
    'build':'estate.site.build',
    'collect-csv':'estate.data.preparation.refresh_public_csv',
    'train-price':'estate.models.nowcast.quantile_v1.train',
    'train-potential':'estate.models.potential.quantile_v3.train',
    'package':'estate.site.local_release',
    'verify-site':'estate.site.verify',
}
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=COMMANDS)
    if len(sys.argv)<2:
        p.print_help();return
    a=p.parse_args(sys.argv[1:2]);sys.argv=[COMMANDS[a.command],*sys.argv[2:]]
    runpy.run_module(COMMANDS[a.command],run_name='__main__')
if __name__=='__main__':main()
