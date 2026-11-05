import { useEffect, useRef, useState } from "react";
import { Pressable, Text, View } from "react-native";

import { errorMessage, readResponse } from "../lib/propertyApi";
import { styles as s } from "../styles/property.styles";
import type { MediaProps } from "./PropertyMedia";

const slots = [
  { kind: "cover", label: "Cover photo", megabytes: 5 },
  { kind: "panorama", label: "360° panorama", megabytes: 12 },
] as const;

type Slot = (typeof slots)[number];

export default function PropertyMedia({
  propertyId, authFetch, version: initialVersion, onBusyChange, editable,
}: MediaProps) {
  const [version, setVersion] = useState(initialVersion);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  async function changeMedia(kind: Slot["kind"], file?: File) {
    if (inFlight.current) return;

    inFlight.current = true;
    setBusy(true);
    onBusyChange(true);
    try {
      const path = `/staff/properties/${propertyId}/media/${kind}?version=${version}`;
      const data = await readResponse(await authFetch(path, {
        method: file ? "PUT" : "DELETE",
        body: file,
      }));
      setVersion(data.version);
    } finally {
      inFlight.current = false;
      setBusy(false);
      onBusyChange(false);
    }
  }

  return (
    <View style={s.panel}>
      <Text style={s.propertyTitle}>{editable ? "Listing photos" : "Listing media preview"}</Text>
      {slots.map((slot) => (
        <MediaSlot
          key={slot.kind}
          slot={slot}
          path={`/staff/properties/${propertyId}/media/${slot.kind}`}
          authFetch={authFetch}
          version={version}
          editable={editable}
          busy={busy}
          onChange={changeMedia}
        />
      ))}
    </View>
  );
}

type SlotProps = {
  slot: Slot;
  path: string;
  authFetch: MediaProps["authFetch"];
  version: number;
  editable?: boolean;
  busy: boolean;
  onChange: (kind: Slot["kind"], file?: File) => Promise<void>;
};

function MediaSlot({ slot, path, authFetch, version, editable, busy, onChange }: SlotProps) {
  const [preview, setPreview] = useState<string | null>();
  const [previewError, setPreviewError] = useState("");
  const [changeError, setChangeError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;

    async function load() {
      setPreview(undefined);
      setPreviewError("");
      try {
        const response = await authFetch(path, { signal: controller.signal });
        if (response.status === 404) {
          if (!controller.signal.aborted) setPreview(null);
          return;
        }
        if (!response.ok) await readResponse(response);
        const image = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(image);
        setPreview(objectUrl);
      } catch (error) {
        if (!controller.signal.aborted) setPreviewError(errorMessage(error));
      }
    }

    void load();
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [authFetch, path, version]);

  async function save(file?: File) {
    setChangeError("");
    setMessage("");
    if (file && file.size > slot.megabytes * 1024 * 1024) {
      setChangeError(`Choose a file up to ${slot.megabytes} MB.`);
      return;
    }
    try {
      await onChange(slot.kind, file);
      setMessage(file ? `${slot.label} saved.` : `${slot.label} removed.`);
    } catch (error) {
      setChangeError(errorMessage(error));
    }
  }

  return (
    <View style={s.panel}>
      <Text style={s.label}>{slot.label}</Text>
      {preview ? (
        <img src={preview} alt={slot.label} style={{ width: "100%", maxHeight: 300, objectFit: "contain" }} />
      ) : null}
      {previewError ? (
        <Text style={s.error} accessibilityRole="alert">{previewError}</Text>
      ) : !preview ? (
        <Text style={s.muted}>{preview === undefined ? "Loading preview…" : "No image uploaded."}</Text>
      ) : null}
      {editable ? (
        <>
          <Text style={s.muted}>
            JPEG, PNG or WebP, up to {slot.megabytes} MB.
            {slot.kind === "panorama" ? " Use a 2:1 equirectangular image at least 1024 pixels wide." : ""}
          </Text>
          <input
            type="file"
            aria-label={`Upload ${slot.label}`}
            accept="image/jpeg,image/png,image/webp"
            disabled={busy}
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              event.currentTarget.value = "";
              if (file) void save(file);
            }}
          />
          {preview ? (
            <Pressable
              accessibilityRole="button"
              style={[s.secondary, busy && s.disabled]}
              disabled={busy}
              onPress={() => void save()}
            >
              <Text style={s.secondaryText}>Remove {slot.label.toLowerCase()}</Text>
            </Pressable>
          ) : null}
        </>
      ) : null}
      {changeError ? <Text style={s.error} accessibilityRole="alert">{changeError}</Text> : null}
      {message ? <Text style={s.success} accessibilityLiveRegion="polite">{message}</Text> : null}
    </View>
  );
}
