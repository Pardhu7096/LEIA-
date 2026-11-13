import { File } from "expo-file-system";
import * as ImagePicker from "expo-image-picker";
import { useEffect, useRef, useState } from "react";
import { Image, Pressable, Text, TextInput, View } from "react-native";

import { errorMessage, readResponse } from "../lib/propertyApi";
import type { StaffFetch } from "../lib/staffSession";
import { styles as s } from "../styles/property.styles";

type MediaProps = {
  propertyId: string;
  version: number;
  authFetch: StaffFetch;
  onBusyChange: (busy: boolean) => void;
  editable?: boolean;
};

type Photo = { id: string; room: string; is_gallery_cover: boolean };
type RoomTour = { id: string; room: string };

const roomSuggestions = ["Bedroom 1", "Bathroom 1", "Kitchen", "Hall / Living room", "Balcony", "Parking", "Exterior"];

export default function PropertyMedia({
  propertyId, version, authFetch, onBusyChange, editable,
}: MediaProps) {
  const latestVersion = useRef(version);
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [room, setRoom] = useState("");
  const [photos, setPhotos] = useState<Photo[] | null>(null);
  const [roomTours, setRoomTours] = useState<RoomTour[]>([]);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const base = `/staff/properties/${propertyId}`;
  const gallery = `${base}/gallery`;
  const panorama = `${base}/media/panorama`;
  const panoramas = `${base}/panoramas`;

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const [data, tours]: [Photo[], RoomTour[]] = await Promise.all([
          readResponse(await authFetch(gallery, { signal: controller.signal })),
          readResponse(await authFetch(panoramas, { signal: controller.signal })),
        ]);
        if (!controller.signal.aborted) {
          setPhotos(data);
          setRoomTours(tours);
          setLoadError("");
        }
      } catch (cause) {
        if (!controller.signal.aborted) setLoadError(errorMessage(cause));
      }
    }
    void load();
    return () => controller.abort();
  }, [authFetch, gallery, panoramas, revision]);

  async function update(path: string, method: string, body?: BodyInit, query = "") {
    const data = await readResponse(await authFetch(
      `${path}?version=${latestVersion.current}${query}`,
      { method, body, headers: body ? { "Content-Type": "application/octet-stream" } : undefined },
    ));
    latestVersion.current = data.version;
  }

  async function change(action: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    onBusyChange(true);
    try {
      await action();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setRevision((value) => value + 1);
      inFlight.current = false;
      setBusy(false);
      onBusyChange(false);
    }
  }

  function upload(isPanorama: boolean) {
    const label = room.trim();
    void change(async () => {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ["images"],
        allowsMultipleSelection: !isPanorama,
        quality: 1,
        preferredAssetRepresentationMode: ImagePicker.UIImagePickerPreferredAssetRepresentationMode.Compatible,
      });
      if (result.canceled) return;
      const limit = isPanorama ? 12 : 5;
      let saved = 0;
      for (const asset of result.assets) {
        const file = asset.file ?? new File(asset.uri);
        try {
          if (file.size > limit * 1024 * 1024) {
            throw new Error(`Choose a JPEG, PNG or WebP up to ${limit} MB.`);
          }
          const body = await file.arrayBuffer();
          if (isPanorama) {
            if (!label) throw new Error("Select a room before uploading its 360° panorama.");
            await update(panoramas + "/" + encodeURIComponent(label), "PUT", body);
          } else {
            await update(gallery, "POST", body, `&room=${encodeURIComponent(label)}`);
          }
          saved += 1;
        } catch (cause) {
          if (saved) setMessage(`Saved ${saved} of ${result.assets.length} photos. Select the remaining photos to retry.`);
          throw cause;
        }
      }
      setMessage(isPanorama ? "360° panorama saved." : `Saved ${saved} photo(s) in ${label}.`);
    });
  }

  const rooms = new Map<string, Photo[]>();
  for (const photo of photos ?? []) {
    const entries = rooms.get(photo.room);
    if (entries) entries.push(photo);
    else rooms.set(photo.room, [photo]);
  }
  const suggestedRooms = [...new Set([...rooms.keys(), ...roomSuggestions])];

  return (
    <View style={s.panel}>
      <Text style={s.propertyTitle}>Room photo gallery</Text>
      {editable && (
        <>
          <Text style={s.muted}>Choose a room or enter a new one, such as Bedroom 2. Add multiple photos per room and choose one for the listing card.</Text>
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
            {suggestedRooms.map((name) => (
              <Pressable key={name} accessibilityRole="button" accessibilityState={{ selected: room === name }} disabled={busy} onPress={() => setRoom(name)} style={s.secondary}>
                <Text style={s.secondaryText}>{name}</Text>
              </Pressable>
            ))}
          </View>
          <TextInput style={s.input} placeholder="Room or area name" accessibilityLabel="Room or area name" value={room} onChangeText={setRoom} editable={!busy} maxLength={60} />
          <MediaButton label="Add photos to room" disabled={busy || !room.trim() || photos === null} onPress={() => upload(false)} />
          <Text style={s.muted}>{photos?.length ?? 0} of 60 photos · JPEG, PNG or WebP up to 5 MB each</Text>
        </>
      )}
      {loadError ? (
        <>
          <Text style={s.error} accessibilityRole="alert">{loadError}</Text>
          <MediaButton label="Reload gallery" disabled={busy} onPress={() => setRevision((value) => value + 1)} />
        </>
      ) : photos === null ? (
        <Text style={s.muted}>Loading photos…</Text>
      ) : photos.length === 0 ? (
        <Text style={s.muted}>No room photos yet.</Text>
      ) : null}
      {[...rooms].map(([name, entries]) => (
        <View key={name} style={s.panel}>
          <Text style={s.label}>{name}</Text>
          {entries.map((photo, index) => {
            const path = `${gallery}/${photo.id}`;
            const label = `${name} photo ${index + 1}`;
            return (
              <View key={photo.id} style={s.panel}>
                <MediaButton label={`${previewPath === path ? "Hide" : "Preview"} ${label}${photo.is_gallery_cover ? " · Cover" : ""}`}
                  onPress={() => setPreviewPath(previewPath === path ? null : path)} />
                {previewPath === path && <MediaPreview path={path} authFetch={authFetch} revision={revision} label={label} />}
                {editable && (
                  <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                    {!photo.is_gallery_cover && <MediaButton label={`Use ${label} as cover`} disabled={busy}
                      onPress={() => void change(() => update(`${path}/cover`, "PUT"))} />}
                    <MediaButton label={`Remove ${label}`} disabled={busy}
                      onPress={() => void change(async () => {
                        await update(path, "DELETE");
                        if (previewPath === path) setPreviewPath(null);
                      })} />
                  </View>
                )}
              </View>
            );
          })}
        </View>
      ))}
      {photos?.length === 0 && <MediaPreview path={`${base}/media/cover`} authFetch={authFetch} revision={revision} label="Existing cover photo" optional />}
      <Text style={s.label}>360° room panoramas</Text>
      <Text style={s.muted}>Upload a 2:1 panorama (minimum 1024px, maximum 12 MB) for each room. Select a room above.</Text>
      {roomTours.map((tour) => (
        <View key={tour.id} style={s.panel}>
          <Text style={s.label}>{tour.room}</Text>
          {editable && <MediaButton label={"Remove " + tour.room + " panorama"} disabled={busy}
            onPress={() => void change(() => update(panoramas + "/" + encodeURIComponent(tour.room), "DELETE"))} />}
        </View>
      ))}
      {editable && <MediaButton label={"Upload 360° for " + (room.trim() || "selected room")}
        disabled={busy || !room.trim()} onPress={() => upload(true)} />}
      <Text style={s.muted}>Legacy panorama (if previously uploaded):</Text>
      <MediaPreview path={panorama} authFetch={authFetch} revision={revision} label="Legacy panorama" optional />
      {busy && <Text style={s.muted}>Working…</Text>}
      {!!message && <Text style={s.success} accessibilityLiveRegion="polite">{message}</Text>}
      {!!error && <Text style={s.error} accessibilityRole="alert">{error}</Text>}
    </View>
  );
}

function MediaButton({ label, disabled, onPress }: { label: string; disabled?: boolean; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress} style={[s.secondary, disabled && s.disabled]}>
      <Text style={s.secondaryText}>{label}</Text>
    </Pressable>
  );
}

function MediaPreview({ path, authFetch, revision, label, optional }: {
  path: string; authFetch: StaffFetch; revision: number; label: string; optional?: boolean;
}) {
  const [uri, setUri] = useState<string | null>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const reader = new FileReader();
    async function load() {
      try {
        const response = await authFetch(path, { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (response.status === 404) {
          setUri(null);
          setError("");
          return;
        }
        if (!response.ok) await readResponse(response);
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        reader.onload = () => {
          if (controller.signal.aborted) return;
          setUri(String(reader.result));
          setError("");
        };
        reader.onerror = () => {
          if (!controller.signal.aborted) setError("Could not preview this image.");
        };
        reader.readAsDataURL(blob);
      } catch (cause) {
        if (!controller.signal.aborted) setError(errorMessage(cause));
      }
    }
    void load();
    return () => controller.abort();
  }, [authFetch, path, revision]);

  if (error) return <Text style={s.error} accessibilityRole="alert">{error}</Text>;
  if (uri) return <Image source={{ uri }} accessibilityLabel={label} resizeMode="contain" style={{ width: "100%", height: 220 }} />;
  if (optional) return null;
  return <Text style={s.muted}>{uri === null ? "No image uploaded." : "Loading image…"}</Text>;
}
