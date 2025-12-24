export interface PatientData {
  fullName: string;
  birthDate: string;
  snils?: string;
  diagnosis: string;
}

export interface ExaminationTemplate {
  id: string;
  clinic: 'dinastiya' | 'pskp';
  patientData: PatientData;
  content: string;
  createdAt: Date;
}

export enum ClinicMode {
  DINASTIYA = 'dinastiya',
  PSKP = 'pskp'
}

export enum BotState {
  IDLE = 'idle',
  AWAITING_DATA = 'awaiting_data',
  AWAITING_CONFIRMATION = 'awaiting_confirmation',
  AWAITING_CORRECTIONS = 'awaiting_corrections',
}

export interface BotContext {
  clinic: ClinicMode;
  state: BotState;
  patientData?: Partial<PatientData>;
  currentTemplate?: {
    content: string;
    patientData: PatientData;
  };
}
