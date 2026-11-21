import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { Camera } from './types';

type Point = Pick<Camera, 'id' | 'name' | 'coordinates' | 'location_note' | 'road'>;

export function CameraMap({ cameras, onSelect }: { cameras: Camera[]; onSelect: (id: string) => void }) {
  const element = useRef<HTMLDivElement>(null);
  const select = useRef(onSelect);
  useEffect(() => { select.current = onSelect; }, [onSelect]);
  const points: Point[] = cameras.filter(camera => camera.coordinates)
    .map(({ id, name, coordinates, location_note, road }) => ({ id, name, coordinates, location_note, road }));
  const locations = JSON.stringify(points);
  useEffect(() => {
    if (!element.current) return;
    const map = L.map(element.current, { scrollWheelZoom: false }).setView([53.11, 158.54], 10);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      referrerPolicy: 'strict-origin-when-cross-origin',
    }).addTo(map);
    const bounds = L.latLngBounds([]);
    for (const point of JSON.parse(locations) as Point[]) {
      if (!point.coordinates) continue;
      const [lon, lat] = point.coordinates;
      const popup = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = point.name;
      popup.append(name);
      if (point.location_note) {
        const description = document.createElement('p');
        description.textContent = point.location_note;
        popup.append(description);
      }
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'map-open';
      open.textContent = 'Открыть камеру';
      open.onclick = () => select.current(point.id);
      popup.append(open);
      L.marker([lat, lon], {
        alt: point.name,
        title: point.name,
        icon: L.divIcon({ className: 'map-point ' + (point.road ? 'legend-road' : 'legend-weather'), iconSize: [17, 17] }),
      }).bindPopup(popup).addTo(map).getElement()?.setAttribute('aria-label', point.name);
      bounds.extend([lat, lon]);
    }
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [28, 28], maxZoom: 14 });
    return () => { map.remove(); };
  }, [locations]);
  return points.length ? <div className="map" ref={element} aria-label="Карта камер и ориентиров" />
    : <p className="empty">Карта появится после подтверждения координат камер.</p>;
}
