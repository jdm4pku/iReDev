from argparse import ArgumentParser
from .iReqDev import iReqDevTeam

def parse_args():
    parser = ArgumentParser(description="Run the iReDev CLI")
    parser.add_argument("--project_name", type=str, default="smart_home",help="Name of the project")
    parser.add_argument("--workspace", type=str, default="output", help="Path to the workspace")
    parser.add_argument(
        "--human_in_loop",
        action="store_true",
        help="Enable human-in-the-loop REngineer feedback after each phase",
    )
    parser.add_argument(
        "--language",
        type=str,
        default="en",
        choices=["zh", "en"],
        help="Output language: 'zh' for Chinese, 'en' for English (default: en)",
    )
    return parser.parse_args()
    
if __name__ == "__main__":
    args = parse_args()
    team = iReqDevTeam(
        project_name=args.project_name,
        output_dir=args.workspace,
        config_path="backend/config",
        human_in_loop=args.human_in_loop,
        language=args.language,
    )
    team.run()