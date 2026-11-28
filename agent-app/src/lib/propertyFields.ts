export type Field = {
  key: string;
  label: string;
  maxLength?: number;
  multiline?: boolean;
  // "multi": several choices (sent as a list); "builder": text with suggestions from /staff/builders.
  kind?: "integer" | "decimal" | "coordinate" | "date" | "multi" | "builder";
  min?: number;
  max?: number;
  choices?: readonly (readonly [string, string])[];
};
const answer = [
  ["unknown", "Unknown / not checked"],
  ["yes", "Yes"],
  ["no", "No"],
  ["not_applicable", "Not applicable"],
] as const;
const source = [
  ["unknown", "Not recorded"],
  ["observed", "Observed during visit"],
  ["owner_reported", "Owner reported"],
  ["document_checked", "Document checked"],
] as const;
const connection = [
  ["unknown", "Unknown / not checked"],
  ["separate", "Separate"],
  ["shared", "Shared"],
  ["not_applicable", "Not applicable"],
] as const;
const money = {
  kind: "integer",
  min: 0,
  max: Number.MAX_SAFE_INTEGER,
} as const;
const evidence = (section: string): Field[] => [
  { key: `${section}_source`, label: "Information source", choices: source },
  {
    key: `${section}_checked_on`,
    label: "Date checked (YYYY-MM-DD)",
    kind: "date",
    maxLength: 10,
  },
];

export const sections: { title: string; fields: Field[] }[] = [
  {
    title: "Property details",
    fields: [
      { key: "title", label: "Property title *", maxLength: 200 },
      {
        key: "property_type",
        label: "Property type *",
        choices: [
          ["flat", "Flat"],
          ["independent_house", "Independent house"],
          ["plot", "Plot"],
        ],
      },
      {
        key: "description",
        label: "Description",
        maxLength: 10000,
        multiline: true,
      },
      { key: "price", label: "Price in rupees (no commas)", ...money, min: 1 },
      { key: "bedrooms", label: "Bedrooms", kind: "integer", min: 0, max: 20 },
      {
        key: "facing",
        label: "Facing",
        choices: [
          ["", "Unknown"],
          ["north", "North"],
          ["northeast", "Northeast"],
          ["east", "East"],
          ["southeast", "Southeast"],
          ["south", "South"],
          ["southwest", "Southwest"],
          ["west", "West"],
          ["northwest", "Northwest"],
        ],
      },
      {
        key: "bathrooms",
        label: "Bathrooms",
        kind: "integer",
        min: 0,
        max: 20,
      },
      {
        key: "area_sqft",
        label: "Area in square feet",
        kind: "decimal",
        min: 0.01,
        max: 9999999999.99,
      },
    ],
  },
  {
    title: "Location",
    fields: [
      {
        key: "address",
        label: "Full address",
        maxLength: 2000,
        multiline: true,
      },
      { key: "locality", label: "Locality", maxLength: 150 },
      { key: "city", label: "City", maxLength: 100 },
      { key: "pincode", label: "PIN code", maxLength: 6 },
      {
        key: "latitude",
        label: "Latitude",
        kind: "coordinate",
        min: -90,
        max: 90,
      },
      {
        key: "longitude",
        label: "Longitude",
        kind: "coordinate",
        min: -180,
        max: 180,
      },
    ],
  },
  {
    title: "Parking",
    fields: [
      {
        key: "car_parking_spaces",
        label: "Car spaces (blank = unknown, 0 = none)",
        kind: "integer",
        min: 0,
        max: 1000,
      },
      {
        key: "bike_parking_spaces",
        label: "Two-wheeler spaces (blank = unknown, 0 = none)",
        kind: "integer",
        min: 0,
        max: 1000,
      },
      {
        key: "parking_type",
        label: "Parking cover",
        choices: [
          ["unknown", "Unknown / not checked"],
          ["none", "No parking"],
          ["open", "Open"],
          ["covered", "Covered"],
          ["both", "Open and covered"],
          ["not_applicable", "Not applicable"],
        ],
      },
      {
        key: "parking_allocation",
        label: "Parking allocation",
        choices: [
          ["unknown", "Unknown / not checked"],
          ["reserved", "Reserved"],
          ["shared", "Shared"],
          ["not_applicable", "Not applicable"],
        ],
      },
      {
        key: "parking_included",
        label: "Parking included in property price?",
        choices: answer,
      },
      {
        key: "parking_extra_cost",
        label: "Extra parking purchase cost (₹)",
        ...money,
      },
      {
        key: "parking_notes",
        label: "Parking notes / visitor access",
        maxLength: 2000,
        multiline: true,
      },
      ...evidence("parking"),
    ],
  },
  {
    title: "Electricity",
    fields: [
      {
        key: "electricity_connection",
        label: "Electricity connected?",
        choices: answer,
      },
      {
        key: "electricity_meter",
        label: "Electricity meter",
        choices: connection,
      },
      {
        key: "electricity_phase",
        label: "Electricity phase",
        choices: [
          ["unknown", "Unknown / not checked"],
          ["single", "Single phase"],
          ["three", "Three phase"],
          ["not_applicable", "Not applicable"],
        ],
      },
      {
        key: "power_backup",
        label: "Power backup coverage",
        choices: [
          ["unknown", "Unknown / not checked"],
          ["none", "No backup"],
          ["common_areas", "Common areas only"],
          ["partial_home", "Some home circuits"],
          ["full_home", "Whole home"],
          ["not_applicable", "Not applicable"],
        ],
      },
      {
        key: "electricity_notes",
        label: "Backup, outages and billing notes",
        maxLength: 2000,
        multiline: true,
      },
      ...evidence("electricity"),
    ],
  },
  {
    title: "Water and maintenance",
    fields: [
      { key: "water_municipal", label: "Municipal water?", choices: answer },
      { key: "water_borewell", label: "Borewell water?", choices: answer },
      { key: "water_tanker", label: "Tanker water?", choices: answer },
      {
        key: "water_supply_frequency",
        label: "Water supply frequency",
        choices: [
          ["unknown", "Unknown / not checked"],
          ["daily", "Daily"],
          ["alternate_days", "Alternate days"],
          ["weekly", "Weekly"],
          ["irregular", "Irregular"],
          ["not_applicable", "Not applicable"],
        ],
      },
      {
        key: "water_storage_litres",
        label: "Water storage capacity (litres)",
        kind: "integer",
        min: 0,
        max: 2147483647,
      },
      {
        key: "water_connection",
        label: "Water connection / meter",
        choices: connection,
      },
      {
        key: "water_monthly_charge",
        label: "Monthly water charge (₹)",
        ...money,
      },
      {
        key: "maintenance_monthly",
        label: "Monthly maintenance charge (₹)",
        ...money,
      },
      {
        key: "water_notes",
        label: "Shortages, other sources and charge notes",
        maxLength: 2000,
        multiline: true,
      },
      ...evidence("water"),
    ],
  },
  {
    title: "Project and builder",
    fields: [
      { key: "builder_name", label: "Builder / developer", maxLength: 200, kind: "builder" },
      { key: "project_name", label: "Project name", maxLength: 200 },
      { key: "rera_number", label: "RERA registration number", maxLength: 50 },
      {
        key: "rera_source",
        label: "RERA status",
        choices: [
          ["unknown", "Not recorded"],
          ["owner_reported", "Reported by owner / builder (not verified)"],
          ["document_checked", "Verified from documents (admin only)"],
        ],
      },
      {
        key: "rera_checked_on",
        label: "RERA checked on (YYYY-MM-DD)",
        kind: "date",
        maxLength: 10,
      },
      {
        key: "construction_status",
        label: "Construction status",
        choices: [
          ["unknown", "Unknown / not checked"],
          ["ready_to_move", "Ready to move"],
          ["under_construction", "Under construction"],
          ["not_applicable", "Not applicable"],
        ],
      },
      {
        key: "possession_date",
        label: "Expected possession (YYYY-MM-DD, under construction only)",
        kind: "date",
        maxLength: 10,
      },
      { key: "floor_number", label: "Property floor (0 = ground)", kind: "integer", min: 0, max: 200 },
      { key: "total_floors", label: "Total floors in building", kind: "integer", min: 1, max: 200 },
    ],
  },
  {
    title: "Building amenities",
    fields: [
      { key: "lift", label: "Lift available?", choices: answer },
      { key: "gated_community", label: "Gated community?", choices: answer },
      {
        key: "security",
        label: "Security",
        choices: [
          ["unknown", "Unknown / not checked"],
          ["none", "None"],
          ["guard", "Security guard"],
          ["cctv", "CCTV"],
          ["guard_and_cctv", "Guard and CCTV"],
          ["not_applicable", "Not applicable"],
        ],
      },
      {
        key: "amenities",
        label: "Amenities confirmed (select all that apply)",
        kind: "multi",
        choices: [
          ["gym", "Gym"],
          ["pool", "Swimming pool"],
          ["clubhouse", "Clubhouse"],
          ["garden", "Garden"],
          ["play_area", "Children's play area"],
          ["community_hall", "Community hall"],
        ],
      },
      {
        key: "project_notes",
        label: "Project notes — staff only",
        maxLength: 2000,
        multiline: true,
      },
      ...evidence("project"),
    ],
  },
  {
    title: "Private owner details",
    fields: [
      { key: "owner_name", label: "Owner name — staff only", maxLength: 150 },
      { key: "owner_phone", label: "Owner phone — staff only", maxLength: 30 },
      {
        key: "agent_notes",
        label: "Agent notes — staff only",
        maxLength: 10000,
        multiline: true,
      },
    ],
  },
];

export const fields = sections.flatMap((section) => section.fields);

// Fields added by migration 014. A draft from a server without them keeps these "not recorded".
export const PROJECT_FACT_KEYS = new Set([
  "builder_name", "project_name", "rera_number", "rera_source", "rera_checked_on",
  "construction_status", "possession_date", "floor_number", "total_floors", "lift",
  "gated_community", "security", "amenities", "project_notes", "project_source", "project_checked_on",
]);

/** Same normalization as the server's builder key: case, spacing and punctuation don't matter. */
export const builderKey = (name: string) => name.toLowerCase().replace(/[^a-z0-9ఀ-౿]/g, "");

/** Up to four existing builders matching what the agent typed (none once it matches exactly). */
export function builderSuggestions(builders: string[], typed: string): string[] {
  const key = builderKey(typed);
  if (!key || builders.some((name) => builderKey(name) === key)) return [];
  return builders.filter((name) => builderKey(name).includes(key)).slice(0, 4);
}
export const initialValues = () =>
  Object.fromEntries(
    fields.map((field) => [
      field.key,
      field.key === "property_type"
        ? "flat"
        : field.kind !== "multi" && field.choices && !field.choices.some(([value]) => value === "")
          ? "unknown"
          : "",
    ]),
  );

// How the saved coordinates were obtained (GEO-M1). Staff-only metadata.
export type LocationMeta =
  | { source: "gps"; accuracyM: number; capturedAt: string }
  | { source: "manual" };

/** Reads location metadata from a loaded draft; tolerant of older servers. */
export function locationMetaFromServer(data: Record<string, unknown>): LocationMeta | null {
  if (data.location_source === "gps"
    && typeof data.location_accuracy_m === "number"
    && typeof data.location_captured_at === "string") {
    return { source: "gps", accuracyM: data.location_accuracy_m, capturedAt: data.location_captured_at };
  }
  if (data.location_source === "manual") return { source: "manual" };
  return null;
}

export function buildPayload(
  values: Record<string, string>,
  consent: boolean,
  location: LocationMeta | null = null,
) {
  const payload: Record<string, string | number | boolean | null | string[]> = {
    listing_consent: consent,
  };
  for (const field of fields) {
    const value = (values[field.key] || "").trim();
    if (field.kind === "multi") {
      const picked = value ? [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))] : [];
      if (picked.some((item) => !field.choices?.some(([choice]) => choice === item))) {
        throw new Error(`Choose valid values for ${field.label}.`);
      }
      payload[field.key] = picked.sort();
      continue;
    }
    payload[field.key] = value || null;
    if (field.maxLength && value.length > field.maxLength) {
      throw new Error(
        `${field.label} must be ${field.maxLength} characters or fewer.`,
      );
    }
    if (field.choices && !field.choices.some((choice) => choice[0] === value)) {
      throw new Error(`Choose a valid value for ${field.label}.`);
    }
    if (!value) continue;
    if (
      field.kind === "integer" ||
      field.kind === "decimal" ||
      field.kind === "coordinate"
    ) {
      const pattern =
        field.kind === "integer"
          ? /^\d+$/
          : field.kind === "decimal"
            ? /^\d+(\.\d{1,2})?$/
            : /^-?\d+(\.\d+)?$/;
      const number = Number(value);
      if (
        !pattern.test(value) ||
        !Number.isFinite(number) ||
        (field.kind === "integer" && !Number.isSafeInteger(number)) ||
        (field.min !== undefined && number < field.min) ||
        (field.max !== undefined && number > field.max)
      ) {
        throw new Error(
          `Enter a valid ${field.label.toLowerCase()} without commas.`,
        );
      }
      // Preserve decimal area exactly for PostgreSQL NUMERIC.
      payload[field.key] = field.kind === "decimal" ? value : number;
    }
    if (field.kind === "date") {
      const parsed = new Date(`${value}T00:00:00Z`);
      if (
        !/^\d{4}-\d{2}-\d{2}$/.test(value) ||
        !Number.isFinite(parsed.getTime()) ||
        parsed.toISOString().slice(0, 10) !== value
      ) {
        throw new Error("Use a valid date in YYYY-MM-DD format.");
      }
    }
  }
  if (!payload.title) throw new Error("Enter a property title.");
  if (
    payload.owner_phone &&
    !/^\+?[0-9][0-9 ()-]{6,28}$/.test(String(payload.owner_phone))
  ) {
    throw new Error("Enter a valid owner phone number.");
  }
  if (
    payload.listing_consent &&
    (!payload.owner_name || !payload.owner_phone)
  ) {
    throw new Error(
      "Record the owner name and phone before confirming listing consent.",
    );
  }
  if (
    Boolean(payload.latitude !== null) !== Boolean(payload.longitude !== null)
  ) {
    throw new Error("Enter both coordinates, or leave both empty.");
  }
  if (payload.pincode && !/^\d{6}$/.test(String(payload.pincode))) {
    throw new Error("PIN code must contain exactly six digits.");
  }
  // Project facts (migration 014): same rules as the server, with plain-language messages.
  if (payload.rera_number && !/^[A-Za-z0-9/_.-]{5,50}$/.test(String(payload.rera_number))) {
    throw new Error("Enter the RERA number exactly as registered (letters, numbers, / . _ - only).");
  }
  if (!payload.rera_number && payload.rera_source !== "unknown") {
    throw new Error("Enter the RERA registration number before setting its status.");
  }
  if (payload.possession_date && payload.construction_status !== "under_construction") {
    throw new Error("Set construction status to under construction to record an expected possession date.");
  }
  if (
    typeof payload.floor_number === "number" &&
    typeof payload.total_floors === "number" &&
    payload.floor_number > payload.total_floors
  ) {
    throw new Error("The property floor can't be above the building's total floors.");
  }
  // Coordinate provenance: GPS metadata travels only with GPS-captured coordinates;
  // typed or edited coordinates are "manual"; no coordinates means no metadata.
  if (payload.latitude === null) {
    payload.location_source = null;
    payload.location_accuracy_m = null;
    payload.location_captured_at = null;
  } else if (location?.source === "gps") {
    payload.location_source = "gps";
    payload.location_accuracy_m = location.accuracyM;
    payload.location_captured_at = location.capturedAt;
  } else {
    payload.location_source = "manual";
    payload.location_accuracy_m = null;
    payload.location_captured_at = null;
  }
  return payload;
}
