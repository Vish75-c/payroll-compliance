// EPFO regional offices, keyed by our two-letter state code. A PF establishment code is
// office (5 letters) + establishment number (7 digits) + extension (3 digits), e.g. MHBAN0012345000.
// Main offices only; "Other office" lets anyone type the 5 letters printed on their PF certificate.
export const PF_OFFICES = {
  MH: [["MHBAN", "Bandra, Mumbai"], ["MHKND", "Kandivali, Mumbai"], ["MHTHN", "Thane"], ["MHVSH", "Vashi, Navi Mumbai"],
    ["PUPUN", "Pune"], ["MHNGP", "Nagpur"], ["MHAUR", "Aurangabad"], ["MHNSK", "Nashik"]],
  KA: [["KNBNG", "Bengaluru"], ["BGBNG", "Bommasandra, Bengaluru"], ["PYKRP", "Koramangala, Bengaluru"], ["KNMNG", "Mangaluru"], ["KNHBL", "Hubballi"]],
  TN: [["TNMAS", "Chennai"], ["TNAMB", "Ambattur, Chennai"], ["TNTBM", "Tambaram, Chennai"], ["TNCBE", "Coimbatore"], ["TNMDU", "Madurai"]],
  TG: [["APHYD", "Hyderabad"], ["TSKKP", "Kukatpally, Hyderabad"], ["APSRP", "Siddipet"]],
  AP: [["APVSP", "Visakhapatnam"], ["APGNT", "Guntur"]],
  DL: [["DLCPM", "Delhi Central"], ["DSNHP", "Delhi South"], ["DLNOR", "Delhi North"], ["DLDWK", "Dwarka, Delhi"]],
  HR: [["GNGGN", "Gurugram"], ["HRFBD", "Faridabad"]],
  UP: [["UPNOI", "Noida"], ["UPKNP", "Kanpur"], ["UPLKO", "Lucknow"]],
  GJ: [["GJAHD", "Ahmedabad"], ["GJVAD", "Vadodara"], ["GJSRT", "Surat"]],
  WB: [["WBCAL", "Kolkata"], ["WBHWH", "Howrah"]],
  RJ: [["RJRAJ", "Jaipur"]],
  KL: [["KRKCH", "Kochi"], ["KRTVM", "Thiruvananthapuram"]],
  MP: [["MPIND", "Indore"], ["MPBPL", "Bhopal"]],
  PB: [["PBLDH", "Ludhiana"]],
};

export const OTHER_OFFICE = "other";

/** Split a stored code into its parts, so an existing company's code fills the three boxes. */
export function splitPfCode(code) {
  const c = String(code || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  const m = c.match(/^([A-Z]{5})(\d{0,7})(\d{0,3})$/);
  return m ? { office: m[1], number: m[2], ext: m[3] } : { office: "", number: "", ext: "" };
}
