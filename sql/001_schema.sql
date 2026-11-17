-- DocFlow practice schema: suppliers, their contacts, documents with line items,
-- deals and the event journal. Dumped with
--   pg_dump -d docflow_dev --schema-only --no-owner --no-privileges
-- Load into an empty database, then apply 002_service_tables.sql:
--   createdb docflow_dev
--   psql -d docflow_dev -f sql/001_schema.sql
--   psql -d docflow_dev -f sql/002_service_tables.sql

--
-- PostgreSQL database dump
--

\restrict EEZM9B4vYYMageMJOlrxwYyPKXbdqHYq34qJowM70gNvTyh67DjG9DHadTqTb7g

-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: deals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deals (
    id bigint NOT NULL,
    document_id bigint NOT NULL,
    status text DEFAULT 'new'::text NOT NULL,
    tech_status text DEFAULT 'pending'::text NOT NULL,
    external_id text,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    last_attempt_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT deals_status_check CHECK ((status = ANY (ARRAY['new'::text, 'in_progress'::text, 'done'::text, 'failed'::text]))),
    CONSTRAINT deals_tech_status_check CHECK ((tech_status = ANY (ARRAY['pending'::text, 'sending'::text, 'created'::text, 'timeout'::text, 'error'::text])))
);


--
-- Name: deals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.deals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: deals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.deals_id_seq OWNED BY public.deals.id;


--
-- Name: document_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_events (
    id bigint NOT NULL,
    document_id bigint NOT NULL,
    event_type text NOT NULL,
    actor text,
    payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: document_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.document_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: document_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.document_events_id_seq OWNED BY public.document_events.id;


--
-- Name: document_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_items (
    id bigint NOT NULL,
    document_id bigint NOT NULL,
    position_no integer,
    name text NOT NULL,
    unit text,
    qty numeric(14,3),
    price numeric(14,2),
    amount numeric(14,2),
    vat_rate numeric(5,2)
);


--
-- Name: document_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.document_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: document_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.document_items_id_seq OWNED BY public.document_items.id;


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id bigint NOT NULL,
    supplier_id bigint,
    doc_type text NOT NULL,
    number text,
    doc_date date,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    total numeric(14,2),
    vat numeric(14,2),
    currency character(3) DEFAULT 'UAH'::bpchar NOT NULL,
    status text DEFAULT 'new'::text NOT NULL,
    file_path text,
    parsed jsonb,
    edited_fields text[],
    edited_at timestamp with time zone,
    edited_by text,
    CONSTRAINT documents_status_check CHECK ((status = ANY (ARRAY['new'::text, 'confirmed'::text, 'sent'::text, 'cancelled'::text])))
);


--
-- Name: documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.documents_id_seq OWNED BY public.documents.id;


--
-- Name: supplier_contacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supplier_contacts (
    id bigint NOT NULL,
    supplier_id bigint NOT NULL,
    name text NOT NULL,
    role text,
    phone text,
    email text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: supplier_contacts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.supplier_contacts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: supplier_contacts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.supplier_contacts_id_seq OWNED BY public.supplier_contacts.id;


--
-- Name: suppliers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.suppliers (
    id bigint NOT NULL,
    name text NOT NULL,
    tax_id text,
    bank_name text,
    iban text,
    address text,
    phone text,
    email text,
    contact_person text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: suppliers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.suppliers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: suppliers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.suppliers_id_seq OWNED BY public.suppliers.id;


--
-- Name: deals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals ALTER COLUMN id SET DEFAULT nextval('public.deals_id_seq'::regclass);


--
-- Name: document_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_events ALTER COLUMN id SET DEFAULT nextval('public.document_events_id_seq'::regclass);


--
-- Name: document_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_items ALTER COLUMN id SET DEFAULT nextval('public.document_items_id_seq'::regclass);


--
-- Name: documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents ALTER COLUMN id SET DEFAULT nextval('public.documents_id_seq'::regclass);


--
-- Name: supplier_contacts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_contacts ALTER COLUMN id SET DEFAULT nextval('public.supplier_contacts_id_seq'::regclass);


--
-- Name: suppliers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suppliers ALTER COLUMN id SET DEFAULT nextval('public.suppliers_id_seq'::regclass);


--
-- Name: deals deals_document_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT deals_document_id_key UNIQUE (document_id);


--
-- Name: deals deals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT deals_pkey PRIMARY KEY (id);


--
-- Name: document_events document_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_events
    ADD CONSTRAINT document_events_pkey PRIMARY KEY (id);


--
-- Name: document_items document_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_items
    ADD CONSTRAINT document_items_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: supplier_contacts supplier_contacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_contacts
    ADD CONSTRAINT supplier_contacts_pkey PRIMARY KEY (id);


--
-- Name: suppliers suppliers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suppliers
    ADD CONSTRAINT suppliers_pkey PRIMARY KEY (id);


--
-- Name: document_events_document_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX document_events_document_idx ON public.document_events USING btree (document_id, created_at);


--
-- Name: document_items_document_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX document_items_document_idx ON public.document_items USING btree (document_id);


--
-- Name: documents_dedupe_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX documents_dedupe_key ON public.documents USING btree (supplier_id, number, doc_date) WHERE ((supplier_id IS NOT NULL) AND (number IS NOT NULL) AND (doc_date IS NOT NULL));


--
-- Name: documents_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX documents_status_idx ON public.documents USING btree (status);


--
-- Name: documents_supplier_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX documents_supplier_idx ON public.documents USING btree (supplier_id);


--
-- Name: supplier_contacts_supplier_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX supplier_contacts_supplier_idx ON public.supplier_contacts USING btree (supplier_id);


--
-- Name: suppliers_tax_id_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX suppliers_tax_id_key ON public.suppliers USING btree (tax_id) WHERE ((tax_id IS NOT NULL) AND (tax_id <> ''::text));


--
-- Name: deals deals_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deals
    ADD CONSTRAINT deals_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: document_events document_events_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_events
    ADD CONSTRAINT document_events_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: document_items document_items_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_items
    ADD CONSTRAINT document_items_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: documents documents_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.suppliers(id);


--
-- Name: supplier_contacts supplier_contacts_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supplier_contacts
    ADD CONSTRAINT supplier_contacts_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.suppliers(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict EEZM9B4vYYMageMJOlrxwYyPKXbdqHYq34qJowM70gNvTyh67DjG9DHadTqTb7g

