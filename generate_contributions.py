import datetime
import random
import subprocess
import os

def main():
    file_path = "contributions.txt"

    start_num = 0
    min_date = None

    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        
        # Parse existing contributions
        for line in lines:
            if line.startswith("Contribution update"):
                try:
                    parts = line.split(" at ")
                    num_part = parts[0].replace("Contribution update ", "")
                    num = int(num_part)
                    if num > start_num:
                        start_num = num
                    
                    date_str = parts[1]
                    # Expecting YYYY-MM-DD HH:MM:SS
                    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                    if min_date is None or dt < min_date:
                        min_date = dt
                except Exception as e:
                    print(f"Error parsing line: {line}. Error: {e}")

    # Default if file parsing fails or is empty
    if not min_date:
        min_date = datetime.datetime.now()

    print(f"Existing contributions count: {start_num}")
    print(f"Oldest contribution date (for backward extrapolation): {min_date}")

    num_to_add = 60
    new_contributions = []

    # Starting from min_date, go backward in time.
    # Subtract between 12 and 36 hours for each step to space them out.
    current_date = min_date - datetime.timedelta(hours=random.randint(12, 24))

    for i in range(num_to_add):
        contrib_num = start_num + i + 1
        date_str = current_date.strftime("%Y-%m-%d %H:%M:%S")
        new_contributions.append((contrib_num, date_str))
        
        # Decrement date for the next step
        current_date -= datetime.timedelta(hours=random.randint(12, 36))

    print(f"Generating {num_to_add} backdated commits...")
    
    # Make commits
    for contrib_num, date_str in new_contributions:
        # Append to contributions.txt
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"Contribution update {contrib_num} at {date_str}\n")
        
        # Git add
        subprocess.run(["git", "add", file_path], check=True)
        
        # Git commit with backdated env vars
        env = os.environ.copy()
        env["GIT_AUTHOR_DATE"] = date_str
        env["GIT_COMMITTER_DATE"] = date_str
        
        msg = f"chore: update contribution log {contrib_num}"
        subprocess.run(["git", "commit", "-m", msg], env=env, check=True)
        print(f"Committed update {contrib_num} with date {date_str}")

    print("Successfully generated all contributions locally.")

if __name__ == "__main__":
    main()
