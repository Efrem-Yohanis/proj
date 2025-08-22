"""
Node 7: Filtration and Validation Rules
This script filters out invalid/unwanted NCC CDRs before sending to downstream.
Expected input: params["records"] -> list of dict CDRs
Expected output: result["valid_records"], result["filtered_records"]
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger("node_filtration_validation")
logger.setLevel(logging.INFO)

def filter_and_validate(records: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
    valid_records = []
    filtered_records = []

    # Get rating group whitelists from node parameters
    data_whitelist = set(params.get("DataCDR_RatingGroup_Whitelist", []))
    voice_whitelist = set(params.get("VoiceCDR_RatingGroup", []))
    sms_mms_whitelist = set(params.get("SMS_MMS_CDR_RatingGroup", []))

    for record in records:
        try:
            # ---- Common Filtration ----
            if record.get("recordType") != "OCSChargingRecord":
                logger.debug("Filtered: non-OCSChargingRecord")
                filtered_records.append(record)
                continue

            mscc_list = record.get("listOfMscc", [])
            if not mscc_list:
                logger.debug("Filtered: missing listOfMscc")
                filtered_records.append(record)
                continue

            # Nested validation for accountInfo/bucketInfo & deviceInfo blocks
            if not (
                record.get("accountInfo")
                and record.get("bucketInfo")
                and any(
                    mscc.get("deviceInfo", {})
                        .get("subscriptionInfo", {})
                        .get("chargingServiceInfo", {})
                        .get("additionalBalanceInfo")
                    for mscc in mscc_list
                )
                and any(
                    mscc.get("groupInfo", {}).get("groupState")
                    for mscc in mscc_list
                )
            ):
                logger.debug("Filtered: missing account/bucket or mscc fields")
                filtered_records.append(record)
                continue

            # ---- Specific CDR Types ----
            # Data CDR check
            if record.get("cdrType") == "DATA":
                if (
                    (not record.get("totalVolumeConsumed"))
                    and record.get("bucketCommitedUnits", 0) == 0
                    and not record.get("accountBalanceCommitted")
                ):
                    logger.debug("Filtered: invalid Data CDR")
                    filtered_records.append(record)
                    continue

            # Voice CDR check
            if record.get("cdrType") == "VOICE":
                if (
                    (not record.get("totalTimeConsumed"))
                    and record.get("bucketCommitedUnits", 0) == 0
                    and not record.get("accountBalanceCommitted")
                ):
                    logger.debug("Filtered: invalid Voice CDR")
                    filtered_records.append(record)
                    continue

            # SMS/USSD/MMS check
            if record.get("cdrType") in {"SMS", "USSD", "MMS"}:
                if (
                    (not record.get("totalUnitsConsumed"))
                    and record.get("bucketCommitedUnits", 0) == 0
                    and not record.get("accountBalanceCommitted")
                ):
                    logger.debug("Filtered: invalid SMS/USSD/MMS CDR")
                    filtered_records.append(record)
                    continue

            # ---- Billing Event Rules ----
            for mscc in mscc_list:
                rating_group = mscc.get("ratingGroup")

                if record.get("cdrType") == "DATA" and rating_group in data_whitelist:
                    logger.debug("Filtered: Data CDR rating group in whitelist")
                    filtered_records.append(record)
                    break
                if record.get("cdrType") == "VOICE" and rating_group in voice_whitelist:
                    logger.debug("Filtered: Voice CDR rating group in whitelist")
                    filtered_records.append(record)
                    break
                if record.get("cdrType") in {"SMS", "MMS"} and rating_group in sms_mms_whitelist:
                    logger.debug("Filtered: SMS/MMS CDR rating group in whitelist")
                    filtered_records.append(record)
                    break
            else:
                # Only apply these if not filtered by whitelist rules
                if not (record.get("EL_SUCCESS") == 1 and record.get("EL_PRE_POST") == "POSTPAID"):
                    logger.debug("Filtered: EL_SUCCESS/EL_PRE_POST rule")
                    filtered_records.append(record)
                    continue

                for mscc in mscc_list:
                    usage_type = (
                        mscc.get("deviceInfo", {})
                           .get("subscriptionInfo", {})
                           .get("chargingServiceInfo", {})
                           .get("additionalBalanceInfo", {})
                           .get("usageType")
                    )
                    if usage_type == "SECONDARY_BALANCE":
                        logger.debug("Filtered: Secondary balance usageType")
                        filtered_records.append(record)
                        break
                else:
                    # If passed all checks
                    valid_records.append(record)

        except Exception as e:
            logger.error(f"Error processing record: {e}")
            filtered_records.append(record)

    return {
        "valid_records": valid_records,
        "filtered_records": filtered_records
    }


# ---------------- Node Entry Point ---------------- #
if __name__ == "__main__":
    import sys, json

    if len(sys.argv) < 3:
        print("Usage: python node7_filtration.py '<records_json>' '<params_json>'")
        sys.exit(1)

    records = json.loads(sys.argv[1])
    params = json.loads(sys.argv[2])

    output = filter_and_validate(records, params)
    print(json.dumps(output, indent=2))
