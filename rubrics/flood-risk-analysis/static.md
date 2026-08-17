### Data Acquisition and Skill Compliance (0.4 points)
- **0.4 points**: Agent correctly uses the `dataretrieval` library to fetch USGS data (parameter `00065` for gage height) and downloads the NWS threshold CSV from the specific URL provided in the skill (`https://water.noaa.gov/resources/downloads/reports/nwps_all_gauges_report.csv`). Crucially, the agent must implement the skill-specific fix for the NWS CSV column mismatch (truncating rows to 43 columns).
- **0.2 points**: Agent fetches data but misses a specific skill requirement, such as using the wrong parameter code for water level, failing to truncate the NWS CSV columns (leading to parsing errors), or using an incorrect source for thresholds.
- **0.0 points**: Agent fails to use the prescribed tools or sources entirely.

### Flood Detection Methodology (0.3 points)
- **0.3 points**: Agent follows the `flood-detection` skill by aggregating instantaneous data to daily values using the daily maximum (`resample('D').max()`) before comparing to the `flood stage` threshold. This demonstrates understanding that flood peaks are captured by maximums, not means.
- **0.15 points**: Agent detects floods but uses an incorrect aggregation method (e.g., daily mean) or fails to aggregate to daily values at all, which contradicts the skill guidance.
- **0.0 points**: Agent does not use thresholds or fails to perform any logical comparison to determine flood status.

### Task Execution and Output Accuracy (0.2 points)
- **0.2 points**: Agent processes the stations from `/root/data/michigan_stations.txt` for the specific window of April 1-7, 2025. The output file `/root/output/flood_results.csv` contains exactly two columns (`station_id`, `flood_days`) and only includes stations where `flood_days >= 1`.
- **0.1 points**: Agent produces the file, but it contains formatting errors, incorrect date ranges, or includes stations with 0 flood days.
- **0.0 points**: Agent fails to produce the output file or the file is completely incorrect.

### Efficiency and Error Handling (0.1 points)
- **0.1 points**: Agent completes the task with minimal trial-and-error. It handles potential issues mentioned in the skills, such as missing thresholds for certain stations or empty dataframes from USGS, without crashing or entering infinite loops.
- **0.0 points**: Agent requires excessive attempts to fix basic syntax/path errors or fails to handle missing data gracefully.
