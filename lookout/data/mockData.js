import { Moon, Trash2, Volume2, Ban, AlertTriangle, ShieldAlert } from "lucide-react";

export const ZONES = [
  "Zone 1",
  "Zone 2",
  "Zone 3",
  "Zone 4",
  "Zone 5",
];

// src/data/mockUsers.js
export const mockUsers = [
  { username: "admin",      password: "admin123",    role: "admin",      name: "Brgy. Administrator" },
  { username: "dispatcher", password: "dispatch123", role: "dispatcher", name: "Dispatch Officer" },
  { username: "officer",    password: "officer123",  role: "officer",    name: "PO2 Mangubat, Lisa" },
];

export const VIOLATION_CONFIG = {
  curfew: { label: "Curfew Violation", color: "#f59e0b", icon: Moon },
  waste: { label: "Waste Violation", color: "#84cc16", icon: Trash2 },
  noise: { label: "Noise Violation", color: "#a78bfa", icon: Volume2 },
  indecency: { label: "Indecent Behavior", color: "#f97316", icon: Ban },
  accident: { label: "Traffic Accident", color: "#ef4444", icon: AlertTriangle },
  intrusion: { label: "Unauthorized Intrusion", color: "#38bdf8", icon: ShieldAlert },
};

export const mockCameras = [
  {
    id: "CAM-01",
    name: "Market Entrance",
    zone: "Zone 1",
    status: "online",
    fps: 28,
    lastMotion: "2 min ago",
    imageUrl: "https://images.unsplash.com/photo-1544739313-6bdb91d32485?w=800&h=450&fit=crop&auto=format",
  },
  {
    id: "CAM-02",
    name: "Barangay Hall Plaza",
    zone: "Zone 2",
    status: "degraded",
    fps: 12,
    lastMotion: "5 min ago",
    imageUrl: "https://images.unsplash.com/photo-1579546929518-9e396f3cc809?w=800&h=450&fit=crop&auto=format",
  },
  {
    id: "CAM-03",
    name: "R.T. Lim Blvd. Junction",
    zone: "Zone 3",
    status: "offline",
    fps: 0,
    lastMotion: "18 min ago",
    imageUrl: "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?w=800&h=450&fit=crop&auto=format",
  },
  {
    id: "CAM-04",
    name: "Purok 6 Alley",
    zone: "Zone 4",
    status: "online",
    fps: 25,
    lastMotion: "1 min ago",
    imageUrl: "https://images.unsplash.com/photo-1515601914948-8493e1a7cf6d?w=800&h=450&fit=crop&auto=format",
  },
];

export const mockAlerts = [
  {
    id: "ALT-0040",
    type: "curfew",
    status: "active",
    camera: "CAM-01",
    cameraZone: "Tetuan Market Entrance",
    timestamp: "2025-06-12T22:14:00",
    confidence: 0.92,
    description: "Minor detected outside curfew hours. Security verified ID before alerting barangay staff.",
    imageUrl: "https://images.unsplash.com/photo-1549402098-f4d9b419c5f8?w=800&h=450&fit=crop&auto=format",
    officerAssigned: "PO2 Mangubat, Lisa",
    suspect: "Santos, Maria",
  },
  {
    id: "ALT-0039",
    type: "noise",
    status: "dispatched",
    camera: "CAM-02",
    cameraZone: "Barangay Hall Plaza",
    timestamp: "2025-06-12T20:05:00",
    confidence: 0.79,
    description: "Loud music and crowd disturbance after curfew. Officer patrol dispatched to investigate.",
    imageUrl: "https://images.unsplash.com/photo-1512966885769-8207b47677df?w=800&h=450&fit=crop&auto=format",
    officerAssigned: "PO3 Cabrera, Dante",
    suspect: "Group in plaza",
  },
  {
    id: "ALT-0038",
    type: "waste",
    status: "acknowledged",
    camera: "CAM-04",
    cameraZone: "Purok 6 Alley",
    timestamp: "2025-06-12T19:12:00",
    confidence: 0.84,
    description: "Illegal dumping detected near residential row. Barangay sanitation team notified.",
    imageUrl: "https://images.unsplash.com/photo-1470420084874-431eb0a8d5b1?w=800&h=450&fit=crop&auto=format",
    officerAssigned: "PO1 Reyes, Marco",
  },
  {
    id: "ALT-0037",
    type: "accident",
    status: "resolved",
    camera: "CAM-03",
    cameraZone: "R.T. Lim Blvd. Junction",
    timestamp: "2025-06-12T18:28:00",
    confidence: 0.96,
    description: "Minor road collision managed by traffic officers. Victim assisted and taken to clinic.",
    imageUrl: "https://images.unsplash.com/photo-1525609004556-c46c7d6cf023?w=800&h=450&fit=crop&auto=format",
    officerAssigned: "PO2 Mangubat, Lisa",
  },
  {
    id: "ALT-0036",
    type: "indecency",
    status: "active",
    camera: "CAM-01",
    cameraZone: "Tetuan Market Entrance",
    timestamp: "2025-06-12T17:55:00",
    confidence: 0.71,
    description: "Potential privacy violation observed near market entrance. Officer verifying before response.",
    imageUrl: "https://images.unsplash.com/photo-1492724441997-5dc865305da7?w=800&h=450&fit=crop&auto=format",
  },
];

export const mockOfficers = [
  { id: "OFC-01", name: "PO2 Mangubat, Lisa", badge: "B-091", status: "responding", location: "Zone 1", phone: "+63 917 555 6921", shift: "6PM - 2AM", joinedDate: "2022-04-11" },
  { id: "OFC-02", name: "PO3 Cabrera, Dante", badge: "B-073", status: "on-duty", location: "Zone 4", phone: "+63 927 221 4845", shift: "7AM - 3PM", joinedDate: "2021-10-02" },
  { id: "OFC-03", name: "PO1 Reyes, Marco", badge: "B-058", status: "on-duty", location: "Zone 3", phone: "+63 934 882 1175", shift: "3PM - 11PM", joinedDate: "2023-01-15" },
  { id: "OFC-04", name: "PO2 Santos, Joy", badge: "B-044", status: "off-duty", location: "Sector 2", phone: "+63 918 876 3308", shift: "10PM - 6AM", joinedDate: "2024-02-20" },
];

export const mockResidents = [
  { id: "RES-01", name: "Angelica Dela Cruz", barangayId: "BRG-TET-0001", age: 28, status: "verified", gender: "female", guardianName: "", imageUrl: "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=200&h=200&fit=crop&auto=format" },
  { id: "RES-02", name: "Kyle Mendoza", barangayId: "BRG-TET-0002", age: 16, status: "pending", gender: "male", guardianName: "Dela Cruz, Ronald", imageUrl: "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=200&h=200&fit=crop&auto=format" },
  { id: "RES-03", name: "Maria Santos", barangayId: "BRG-TET-0003", age: 42, status: "flagged", gender: "female", guardianName: "", imageUrl: "https://images.unsplash.com/photo-1544723795-3fb6469f5b39?w=200&h=200&fit=crop&auto=format" },
];

export const mockHouseholds = [

  {
    id: "HH-TET-0001",
    familyName: "Angeles",
    purok: "Purok 2",
    address: "142 Don Maria Drive",
    zone: "Zone 2",
    contact: "+63 917 000 1122",
    enrolledDate: "2025-06-10",
    members: [
      { id: "MEM-001", firstName: "Peter", lastName: "Angeles", birthdate: "1990-03-12", barangayId: "BRG-TET-0101", status: "verified", relation: "Head", imageUrl: "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=200&h=200&fit=crop&auto=format" },
      { id: "MEM-002", firstName: "Ana", lastName: "Angeles", birthdate: "1994-08-21", barangayId: "BRG-TET-0102", status: "verified", relation: "Spouse", imageUrl: "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=200&h=200&fit=crop&auto=format" },
      { id: "MEM-003", firstName: "Leo", lastName: "Angeles", birthdate: "2011-11-30", barangayId: "BRG-TET-0103", status: "pending", relation: "Son", imageUrl: "https://images.unsplash.com/photo-1517841905240-472988babdf9?w=200&h=200&fit=crop&auto=format" },
    ],
    guardianLinks: [
      { minorId: "MEM-003", guardianIds: ["MEM-001", "MEM-002"] },
    ],
  },
];

export const hourlyViolations = [
  { hour: "00:00", curfew: 2, waste: 0, noise: 1, indecency: 0, accident: 0 },
  { hour: "03:00", curfew: 1, waste: 1, noise: 0, indecency: 0, accident: 0 },
  { hour: "06:00", curfew: 0, waste: 1, noise: 0, indecency: 0, accident: 0 },
  { hour: "09:00", curfew: 0, waste: 0, noise: 1, indecency: 0, accident: 0 },
  { hour: "12:00", curfew: 0, waste: 0, noise: 1, indecency: 0, accident: 0 },
  { hour: "15:00", curfew: 0, waste: 1, noise: 0, indecency: 0, accident: 0 },
  { hour: "18:00", curfew: 1, waste: 0, noise: 0, indecency: 1, accident: 1 },
  { hour: "21:00", curfew: 1, waste: 0, noise: 1, indecency: 0, accident: 0 },
];

export const weeklyTrend = [
  { day: "Mon", violations: 10 },
  { day: "Tue", violations: 14 },
  { day: "Wed", violations: 8 },
  { day: "Thu", violations: 12 },
  { day: "Fri", violations: 16 },
  { day: "Sat", violations: 22 },
  { day: "Sun", violations: 18 },
];
