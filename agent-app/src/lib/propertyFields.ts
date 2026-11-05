export type Field = {
  key: string;
  label: string;
  maxLength?: number;
  multiline?: boolean;
  kind?: "integer" | "decimal" | "coordinate" | "date";
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
export const initialValues = () =>
  Object.fromEntries(
    fields.map((field) => [
      field.key,
      field.key === "property_type" ? "flat" : field.choices ? "unknown" : "",
    ]),
  );

export function buildPayload(values: Record<string, string>, consent: boolean) {
  const payload: Record<string, string | number | boolean | null> = {
    listing_consent: consent,
  };
  for (const field of fields) {
    const value = (values[field.key] || "").trim();
    payload[field.key] = value || null;
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
    Boolean(payload.latitude !== null) !== Boolean(payload.longitude !== null)
  ) {
    throw new Error("Enter both coordinates, or leave both empty.");
  }
  if (payload.pincode && !/^\d{6}$/.test(String(payload.pincode))) {
    throw new Error("PIN code must contain exactly six digits.");
  }
  return payload;
}
