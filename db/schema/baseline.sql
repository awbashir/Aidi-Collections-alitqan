-- CAWP schema-only baseline (structure only: no rows, no owners, no grants).
-- Taken 2026-09-23 from the local cawp-db (restored 2026-09-15 production dump + db/01..03)
-- BEFORE any db/migrations/* were applied, with:
--   pg_dump -U postgres -d mydata --schema-only --no-owner --no-privileges --no-comments
-- The psql-only \restrict/\unrestrict lines (pg_dump >= 16.10) were removed so the file is
-- stable and loadable by `manage_db.py load-schema` / any psql version.
-- Used by the pytest harness and CI only. Never load it into a database that has data.
--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

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

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: clints_refresh_last_comment(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.clints_refresh_last_comment(p_clint uuid) RETURNS void
    LANGUAGE sql
    AS $$
  UPDATE clints c
     SET last_comment_at = lc.create_at,
         last_comment_id = lc.id
    FROM (SELECT p_clint AS clint_id,
                 (SELECT m.create_at FROM "Comments" m WHERE m.clint_id = p_clint AND m.is_deleted = false
                   ORDER BY m.create_at DESC, m.id DESC LIMIT 1) AS create_at,
                 (SELECT m.id FROM "Comments" m WHERE m.clint_id = p_clint AND m.is_deleted = false
                   ORDER BY m.create_at DESC, m.id DESC LIMIT 1) AS id) lc
   WHERE c.id = lc.clint_id
     AND (c.last_comment_at IS DISTINCT FROM lc.create_at OR c.last_comment_id IS DISTINCT FROM lc.id);
$$;


--
-- Name: trg_comments_last_comment(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_comments_last_comment() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM clints_refresh_last_comment(OLD.clint_id);
    RETURN OLD;
  END IF;
  PERFORM clints_refresh_last_comment(NEW.clint_id);
  IF TG_OP = 'UPDATE' AND NEW.clint_id IS DISTINCT FROM OLD.clint_id THEN
    PERFORM clints_refresh_last_comment(OLD.clint_id);
  END IF;
  RETURN NEW;
END $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ClintContactTypes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."ClintContactTypes" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL
);


--
-- Name: ClintContacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."ClintContacts" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    contact_type_id uuid NOT NULL,
    "identityNumber" text NOT NULL,
    contact_status text NOT NULL,
    contact text NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_archive boolean DEFAULT false NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    is_default boolean DEFAULT false NOT NULL
)
WITH (autovacuum_vacuum_scale_factor='0.02', autovacuum_analyze_scale_factor='0.01');


--
-- Name: CollectionMethods; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."CollectionMethods" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL
);


--
-- Name: CollectionTypes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."CollectionTypes" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL
);


--
-- Name: CommentStatuses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."CommentStatuses" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    contract_id uuid,
    "CommentReachability_id" uuid,
    color text,
    is_active boolean DEFAULT false,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: Comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."Comments" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    clint_id uuid NOT NULL,
    user_id uuid NOT NULL,
    "contactMethod_id" uuid NOT NULL,
    comment text NOT NULL,
    comment_status_id uuid NOT NULL,
    comment_reachability_id uuid,
    call_id text,
    next_contact_date timestamp with time zone,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_archive boolean DEFAULT false NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    "contactNumber" text
)
WITH (autovacuum_vacuum_scale_factor='0.02', autovacuum_analyze_scale_factor='0.01');


--
-- Name: ContactMethods; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."ContactMethods" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL
);


--
-- Name: Payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."Payments" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    clint_id uuid NOT NULL,
    user_id uuid NOT NULL,
    collection_type_id uuid NOT NULL,
    collection_method_id uuid NOT NULL,
    amount double precision NOT NULL,
    payment_date timestamp with time zone NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    is_archive boolean DEFAULT false NOT NULL,
    approval_status boolean,
    approval_by uuid,
    approval_at timestamp with time zone,
    approval_comment text
);


--
-- Name: SepcifyUsers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."SepcifyUsers" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    clint_id uuid NOT NULL,
    create_by uuid,
    is_deleted boolean DEFAULT false NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL
)
WITH (autovacuum_vacuum_scale_factor='0.02', autovacuum_analyze_scale_factor='0.01');


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: archiveReason; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."archiveReason" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL
);


--
-- Name: branches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.branches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: client_lock_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.client_lock_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    clint_id uuid NOT NULL,
    user_id uuid NOT NULL,
    action text NOT NULL,
    reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: clintArchive; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."clintArchive" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    clint_id uuid NOT NULL,
    user_id uuid NOT NULL,
    archive_reason_id uuid NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: clint_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clint_documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    clint_id uuid NOT NULL,
    file_name text NOT NULL,
    original_name text NOT NULL,
    display_name text,
    file_type text NOT NULL,
    uploaded_by uuid,
    is_deleted boolean DEFAULT false NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: clints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clints (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    "identityNumber" text NOT NULL,
    "accountNumber" text NOT NULL,
    amount double precision NOT NULL,
    attribute jsonb NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL,
    user_id uuid,
    is_active boolean DEFAULT true NOT NULL,
    is_archive boolean DEFAULT false NOT NULL,
    portfolios_id uuid NOT NULL,
    "clintArchive_id" uuid,
    "delinquencyReason_id" uuid,
    is_locked boolean DEFAULT false NOT NULL,
    last_comment_at timestamp with time zone,
    last_comment_id uuid
)
WITH (autovacuum_vacuum_scale_factor='0.02', autovacuum_analyze_scale_factor='0.01');


--
-- Name: comment_reachability; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comment_reachability (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: contracts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contracts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    monthly_target_percent double precision DEFAULT '0'::double precision,
    monthly_target_amount double precision DEFAULT '0'::double precision,
    monthly_target_is_percentage boolean DEFAULT false
);


--
-- Name: delinquencyReason; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."delinquencyReason" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL
);


--
-- Name: login_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.login_log (
    id integer NOT NULL,
    username character varying(255) NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    ip_address character varying(100),
    outcome character varying(50)
);


--
-- Name: login_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.login_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: login_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.login_log_id_seq OWNED BY public.login_log.id;


--
-- Name: nationalities; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.nationalities (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    id integer NOT NULL,
    event text NOT NULL,
    data json,
    sender_user_id uuid NOT NULL,
    receiver_user_id uuid NOT NULL,
    is_read boolean,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notifications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notifications_id_seq OWNED BY public.notifications.id;


--
-- Name: paymentDates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public."paymentDates" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    clint_id uuid NOT NULL,
    user_id uuid NOT NULL,
    payment_date timestamp with time zone NOT NULL,
    amount double precision NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL,
    is_archive boolean DEFAULT false NOT NULL
);


--
-- Name: portfolios; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portfolios (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    create_at timestamp with time zone DEFAULT now() NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL,
    contract_id uuid NOT NULL
);


--
-- Name: roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    permission json DEFAULT '{"dashbord": false, "dashbord_admin": false, "dashbord_user": false, "branches": false, "branches_add": false, "branches_edit": false, "branches_delete": false, "nationalities": false, "nationalities_add": false, "nationalities_edit": false, "nationalities_delete": false, "roles": false, "roles_add": false, "roles_edit": false, "roles_delete": false, "users": false, "users_add": false, "users_edit": false, "users_delete": false, "contracts": false, "contracts_add": false, "contracts_edit": false, "contracts_delete": false, "clints": false, "clints_add": false, "clints_edit": false, "clints_sepcifyUser": false, "clints_move_archive": false, "clints_UpdateByExcel": false, "clints_delete": false, "clints_details": false, "clints_comments": false, "clints_Payments": false, "clints_PayemntDates": false, "supervisors": false, "supervisors_add": false, "supervisors_edit": false, "supervisors_delete": false, "collections": false, "collections_users": false, "reports": false, "reports_users": false, "report_users_comments": false, "report_users_paymentDates": false, "reports_clints": false, "report_clintes_active": false, "report_clintes_inactive": false, "report_portfolios": false, "report_quality_neglect": false, "settings_general_archiveReasons": false, "settings_general_archiveReasons_add": false, "settings_general_archiveReasons_edit": false, "settings_general_archiveReasons_delete": false, "settings_general_collectionMethods": false, "settings_general_collectionMethods_add": false, "settings_general_collectionMethods_edit": false, "settings_general_collectionMethods_delete": false, "settings_general_nationalities": false, "settings_general_nationalities_add": false, "settings_general_nationalities_edit": false, "settings_general_nationalities_delete": false, "settings_general_updateSide": false, "settings_general_updateSide_add": false, "settings_general_updateSide_edit": false, "settings_general_updateSide_delete": false, "clints_archive": false, "clints_archive_move": false, "portfolios": false, "portfolios_add": false, "portfolios_edit": false, "portfolios_delete": false, "portfolios_details": false, "settings": false, "settings_comments": false, "settings_comments_status": false, "settings_comments_status_add": false, "settings_comments_status_edit": false, "settings_comments_status_delete": false, "accounts": false, "accounts_payments": false, "accounts_payments_edit": false, "accounts_payments_delete": false}'::json NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: supervisor_contracts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supervisor_contracts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    supervisor_id uuid NOT NULL,
    contract_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: supervisor_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supervisor_users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    supervisor_id uuid NOT NULL,
    user_id uuid NOT NULL,
    update_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: supervisors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.supervisors (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: updatesides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.updatesides (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    frist_name text NOT NULL,
    last_name text NOT NULL,
    mobile text NOT NULL,
    password text NOT NULL,
    user_name text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    role_id uuid NOT NULL,
    contract_id uuid,
    extension text,
    nationality_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    "identityNumber" text,
    target_is_percentage boolean DEFAULT false,
    target_amount double precision DEFAULT '0'::double precision,
    target_percent double precision DEFAULT '0'::double precision
);


--
-- Name: login_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_log ALTER COLUMN id SET DEFAULT nextval('public.login_log_id_seq'::regclass);


--
-- Name: notifications id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications ALTER COLUMN id SET DEFAULT nextval('public.notifications_id_seq'::regclass);


--
-- Name: ClintContactTypes ClintContactTypes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."ClintContactTypes"
    ADD CONSTRAINT "ClintContactTypes_pkey" PRIMARY KEY (id);


--
-- Name: ClintContacts ClintContacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."ClintContacts"
    ADD CONSTRAINT "ClintContacts_pkey" PRIMARY KEY (id);


--
-- Name: CollectionMethods CollectionMethods_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."CollectionMethods"
    ADD CONSTRAINT "CollectionMethods_pkey" PRIMARY KEY (id);


--
-- Name: CollectionTypes CollectionTypes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."CollectionTypes"
    ADD CONSTRAINT "CollectionTypes_pkey" PRIMARY KEY (id);


--
-- Name: CommentStatuses CommentStatuses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."CommentStatuses"
    ADD CONSTRAINT "CommentStatuses_pkey" PRIMARY KEY (id);


--
-- Name: Comments Comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Comments"
    ADD CONSTRAINT "Comments_pkey" PRIMARY KEY (id);


--
-- Name: ContactMethods ContactMethods_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."ContactMethods"
    ADD CONSTRAINT "ContactMethods_pkey" PRIMARY KEY (id);


--
-- Name: Payments Payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Payments"
    ADD CONSTRAINT "Payments_pkey" PRIMARY KEY (id);


--
-- Name: SepcifyUsers SepcifyUsers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."SepcifyUsers"
    ADD CONSTRAINT "SepcifyUsers_pkey" PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: archiveReason archiveReason_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."archiveReason"
    ADD CONSTRAINT "archiveReason_pkey" PRIMARY KEY (id);


--
-- Name: branches branches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.branches
    ADD CONSTRAINT branches_pkey PRIMARY KEY (id);


--
-- Name: client_lock_history client_lock_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_lock_history
    ADD CONSTRAINT client_lock_history_pkey PRIMARY KEY (id);


--
-- Name: clintArchive clintArchive_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."clintArchive"
    ADD CONSTRAINT "clintArchive_pkey" PRIMARY KEY (id);


--
-- Name: clint_documents clint_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clint_documents
    ADD CONSTRAINT clint_documents_pkey PRIMARY KEY (id);


--
-- Name: clints clints_accountNumber_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clints
    ADD CONSTRAINT "clints_accountNumber_key" UNIQUE ("accountNumber");


--
-- Name: clints clints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clints
    ADD CONSTRAINT clints_pkey PRIMARY KEY (id);


--
-- Name: comment_reachability comment_reachability_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comment_reachability
    ADD CONSTRAINT comment_reachability_pkey PRIMARY KEY (id);


--
-- Name: contracts contracts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_pkey PRIMARY KEY (id);


--
-- Name: delinquencyReason delinquencyReason_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."delinquencyReason"
    ADD CONSTRAINT "delinquencyReason_pkey" PRIMARY KEY (id);


--
-- Name: login_log login_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.login_log
    ADD CONSTRAINT login_log_pkey PRIMARY KEY (id);


--
-- Name: nationalities nationalities_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.nationalities
    ADD CONSTRAINT nationalities_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: paymentDates paymentDates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."paymentDates"
    ADD CONSTRAINT "paymentDates_pkey" PRIMARY KEY (id);


--
-- Name: portfolios portfolios_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT portfolios_pkey PRIMARY KEY (id);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: supervisor_contracts supervisor_contracts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supervisor_contracts
    ADD CONSTRAINT supervisor_contracts_pkey PRIMARY KEY (id);


--
-- Name: supervisor_users supervisor_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supervisor_users
    ADD CONSTRAINT supervisor_users_pkey PRIMARY KEY (id);


--
-- Name: supervisors supervisors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supervisors
    ADD CONSTRAINT supervisors_pkey PRIMARY KEY (id);


--
-- Name: updatesides updatesides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.updatesides
    ADD CONSTRAINT updatesides_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_user_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_user_name_key UNIQUE (user_name);


--
-- Name: ix_clint_documents_clint_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clint_documents_clint_id ON public.clint_documents USING btree (clint_id);


--
-- Name: ix_clintarchive_clint_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clintarchive_clint_id ON public."clintArchive" USING btree (clint_id);


--
-- Name: ix_clintarchive_create; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clintarchive_create ON public."clintArchive" USING btree (create_at DESC);


--
-- Name: ix_clintarchive_reason; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clintarchive_reason ON public."clintArchive" USING btree (archive_reason_id);


--
-- Name: ix_clintarchive_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clintarchive_user_id ON public."clintArchive" USING btree (user_id);


--
-- Name: ix_clintcontacts_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clintcontacts_identity ON public."ClintContacts" USING btree ("identityNumber");


--
-- Name: ix_clintcontacts_identity_default; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clintcontacts_identity_default ON public."ClintContacts" USING btree ("identityNumber") WHERE ((is_default = true) AND (is_deleted = false));


--
-- Name: ix_clints_archive_create; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_archive_create ON public.clints USING btree (create_at DESC) WHERE (is_archive = true);


--
-- Name: ix_clints_archive_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_archive_id ON public.clints USING btree ("clintArchive_id");


--
-- Name: ix_clints_create_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_create_at ON public.clints USING btree (create_at DESC);


--
-- Name: ix_clints_delinquency; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_delinquency ON public.clints USING btree ("delinquencyReason_id") WHERE ("delinquencyReason_id" IS NOT NULL);


--
-- Name: ix_clints_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_identity ON public.clints USING btree ("identityNumber");


--
-- Name: ix_clints_identity_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_identity_trgm ON public.clints USING gin ("identityNumber" public.gin_trgm_ops);


--
-- Name: ix_clints_last_comment_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_last_comment_id ON public.clints USING btree (last_comment_id) WHERE (last_comment_id IS NOT NULL);


--
-- Name: ix_clints_last_comment_live; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_last_comment_live ON public.clints USING btree (last_comment_at DESC NULLS LAST) WHERE (is_archive = false);


--
-- Name: ix_clints_last_comment_live_asc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_last_comment_live_asc ON public.clints USING btree (last_comment_at) WHERE (is_archive = false);


--
-- Name: ix_clints_live_create; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_live_create ON public.clints USING btree (create_at DESC) WHERE (is_archive = false);


--
-- Name: ix_clints_name_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_name_trgm ON public.clints USING gin (name public.gin_trgm_ops);


--
-- Name: ix_clints_portfolios_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_portfolios_id ON public.clints USING btree (portfolios_id);


--
-- Name: ix_clints_portfolios_notarch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_portfolios_notarch ON public.clints USING btree (portfolios_id) WHERE (is_archive = false);


--
-- Name: ix_clints_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_user_id ON public.clints USING btree (user_id);


--
-- Name: ix_clints_user_last_comment_live; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_user_last_comment_live ON public.clints USING btree (user_id, last_comment_at DESC NULLS LAST) WHERE (is_archive = false);


--
-- Name: ix_clints_user_notarch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_clints_user_notarch ON public.clints USING btree (user_id) WHERE (is_archive = false);


--
-- Name: ix_comments_clint_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_clint_created ON public."Comments" USING btree (clint_id, create_at DESC) WHERE (is_deleted = false);


--
-- Name: ix_comments_clint_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_clint_id ON public."Comments" USING btree (clint_id);


--
-- Name: ix_comments_create_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_create_at ON public."Comments" USING btree (create_at DESC) WHERE (is_deleted = false);


--
-- Name: ix_comments_next_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_next_contact ON public."Comments" USING btree (user_id, next_contact_date) WHERE ((next_contact_date IS NOT NULL) AND (is_deleted = false));


--
-- Name: ix_comments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_status ON public."Comments" USING btree (comment_status_id);


--
-- Name: ix_comments_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_user_created ON public."Comments" USING btree (user_id, create_at DESC) WHERE (is_deleted = false);


--
-- Name: ix_comments_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_user_id ON public."Comments" USING btree (user_id);


--
-- Name: ix_commentstatuses_contract_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commentstatuses_contract_id ON public."CommentStatuses" USING btree (contract_id);


--
-- Name: ix_commentstatuses_reachability; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_commentstatuses_reachability ON public."CommentStatuses" USING btree ("CommentReachability_id");


--
-- Name: ix_lockhistory_clint_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lockhistory_clint_created ON public.client_lock_history USING btree (clint_id, created_at DESC);


--
-- Name: ix_lockhistory_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lockhistory_user ON public.client_lock_history USING btree (user_id);


--
-- Name: ix_loginlog_username_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_loginlog_username_ts ON public.login_log USING btree (username, "timestamp" DESC);


--
-- Name: ix_notifications_receiver_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_receiver_created ON public.notifications USING btree (receiver_user_id, created_at DESC);


--
-- Name: ix_notifications_receiver_unread; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_receiver_unread ON public.notifications USING btree (receiver_user_id) WHERE (is_read IS NOT TRUE);


--
-- Name: ix_notifications_sender; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_sender ON public.notifications USING btree (sender_user_id);


--
-- Name: ix_paymentdates_clint_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_paymentdates_clint_id ON public."paymentDates" USING btree (clint_id);


--
-- Name: ix_paymentdates_payment_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_paymentdates_payment_date ON public."paymentDates" USING btree (payment_date) WHERE (is_deleted = false);


--
-- Name: ix_paymentdates_user_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_paymentdates_user_date ON public."paymentDates" USING btree (user_id, payment_date) WHERE (is_deleted = false);


--
-- Name: ix_paymentdates_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_paymentdates_user_id ON public."paymentDates" USING btree (user_id);


--
-- Name: ix_payments_approval_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_approval_by ON public."Payments" USING btree (approval_by) WHERE (approval_by IS NOT NULL);


--
-- Name: ix_payments_approval_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_approval_status ON public."Payments" USING btree (approval_status, create_at DESC) WHERE (is_deleted = false);


--
-- Name: ix_payments_clint_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_clint_id ON public."Payments" USING btree (clint_id);


--
-- Name: ix_payments_collection_method; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_collection_method ON public."Payments" USING btree (collection_method_id);


--
-- Name: ix_payments_collection_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_collection_type ON public."Payments" USING btree (collection_type_id);


--
-- Name: ix_payments_create_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_create_at ON public."Payments" USING btree (create_at DESC) WHERE (is_deleted = false);


--
-- Name: ix_payments_payment_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_payment_date ON public."Payments" USING btree (payment_date) WHERE (is_deleted = false);


--
-- Name: ix_payments_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_user_created ON public."Payments" USING btree (user_id, create_at DESC) WHERE (is_deleted = false);


--
-- Name: ix_payments_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_payments_user_id ON public."Payments" USING btree (user_id);


--
-- Name: ix_portfolios_contract_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_portfolios_contract_id ON public.portfolios USING btree (contract_id);


--
-- Name: ix_sepcifyusers_user_clint; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sepcifyusers_user_clint ON public."SepcifyUsers" USING btree (user_id, clint_id);


--
-- Name: ix_specify_clint_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_specify_clint_created ON public."SepcifyUsers" USING btree (clint_id, create_at DESC) WHERE (is_deleted = false);


--
-- Name: ix_specifyusers_clint_notdel; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_specifyusers_clint_notdel ON public."SepcifyUsers" USING btree (clint_id, is_deleted);


--
-- Name: ix_supcontracts_sup_contract; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supcontracts_sup_contract ON public.supervisor_contracts USING btree (supervisor_id, contract_id);


--
-- Name: ix_supcontracts_sup_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supcontracts_sup_id ON public.supervisor_contracts USING btree (supervisor_id);


--
-- Name: ix_supervisor_contracts_contract_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supervisor_contracts_contract_id ON public.supervisor_contracts USING btree (contract_id);


--
-- Name: ix_supervisor_users_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supervisor_users_user_id ON public.supervisor_users USING btree (user_id);


--
-- Name: ix_supervisors_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supervisors_user_id ON public.supervisors USING btree (user_id);


--
-- Name: ix_supusers_sup_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supusers_sup_id ON public.supervisor_users USING btree (supervisor_id);


--
-- Name: ix_supusers_sup_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_supusers_sup_user ON public.supervisor_users USING btree (supervisor_id, user_id);


--
-- Name: ix_users_branch_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_branch_id ON public.users USING btree (branch_id);


--
-- Name: ix_users_contract_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_contract_id ON public.users USING btree (contract_id);


--
-- Name: ix_users_nationality_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_nationality_id ON public.users USING btree (nationality_id);


--
-- Name: ix_users_role_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_users_role_id ON public.users USING btree (role_id);


--
-- Name: Comments comments_last_comment_trg; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER comments_last_comment_trg AFTER INSERT OR DELETE OR UPDATE OF create_at, is_deleted, clint_id ON public."Comments" FOR EACH ROW EXECUTE FUNCTION public.trg_comments_last_comment();


--
-- Name: ClintContacts ClintContacts_contact_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."ClintContacts"
    ADD CONSTRAINT "ClintContacts_contact_type_id_fkey" FOREIGN KEY (contact_type_id) REFERENCES public."ClintContactTypes"(id);


--
-- Name: CommentStatuses CommentStatuses_CommentReachability_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."CommentStatuses"
    ADD CONSTRAINT "CommentStatuses_CommentReachability_id_fkey" FOREIGN KEY ("CommentReachability_id") REFERENCES public.comment_reachability(id);


--
-- Name: CommentStatuses CommentStatuses_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."CommentStatuses"
    ADD CONSTRAINT "CommentStatuses_contract_id_fkey" FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: Comments Comments_clint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Comments"
    ADD CONSTRAINT "Comments_clint_id_fkey" FOREIGN KEY (clint_id) REFERENCES public.clints(id) ON DELETE CASCADE;


--
-- Name: Comments Comments_comment_reachability_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Comments"
    ADD CONSTRAINT "Comments_comment_reachability_id_fkey" FOREIGN KEY (comment_reachability_id) REFERENCES public.comment_reachability(id);


--
-- Name: Comments Comments_comment_status_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Comments"
    ADD CONSTRAINT "Comments_comment_status_id_fkey" FOREIGN KEY (comment_status_id) REFERENCES public."CommentStatuses"(id);


--
-- Name: Comments Comments_contactMethod_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Comments"
    ADD CONSTRAINT "Comments_contactMethod_id_fkey" FOREIGN KEY ("contactMethod_id") REFERENCES public."ContactMethods"(id);


--
-- Name: Comments Comments_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Comments"
    ADD CONSTRAINT "Comments_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: Payments Payments_approval_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Payments"
    ADD CONSTRAINT "Payments_approval_by_fkey" FOREIGN KEY (approval_by) REFERENCES public.users(id);


--
-- Name: Payments Payments_clint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Payments"
    ADD CONSTRAINT "Payments_clint_id_fkey" FOREIGN KEY (clint_id) REFERENCES public.clints(id) ON DELETE CASCADE;


--
-- Name: Payments Payments_collection_method_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Payments"
    ADD CONSTRAINT "Payments_collection_method_id_fkey" FOREIGN KEY (collection_method_id) REFERENCES public."CollectionMethods"(id);


--
-- Name: Payments Payments_collection_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Payments"
    ADD CONSTRAINT "Payments_collection_type_id_fkey" FOREIGN KEY (collection_type_id) REFERENCES public."CollectionTypes"(id);


--
-- Name: Payments Payments_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."Payments"
    ADD CONSTRAINT "Payments_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: SepcifyUsers SepcifyUsers_clint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."SepcifyUsers"
    ADD CONSTRAINT "SepcifyUsers_clint_id_fkey" FOREIGN KEY (clint_id) REFERENCES public.clints(id) ON DELETE CASCADE;


--
-- Name: SepcifyUsers SepcifyUsers_create_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."SepcifyUsers"
    ADD CONSTRAINT "SepcifyUsers_create_by_fkey" FOREIGN KEY (create_by) REFERENCES public.users(id);


--
-- Name: SepcifyUsers SepcifyUsers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."SepcifyUsers"
    ADD CONSTRAINT "SepcifyUsers_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: client_lock_history client_lock_history_clint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_lock_history
    ADD CONSTRAINT client_lock_history_clint_id_fkey FOREIGN KEY (clint_id) REFERENCES public.clints(id) ON DELETE CASCADE;


--
-- Name: client_lock_history client_lock_history_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_lock_history
    ADD CONSTRAINT client_lock_history_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: clintArchive clintArchive_archive_reason_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."clintArchive"
    ADD CONSTRAINT "clintArchive_archive_reason_id_fkey" FOREIGN KEY (archive_reason_id) REFERENCES public."archiveReason"(id);


--
-- Name: clintArchive clintArchive_clint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."clintArchive"
    ADD CONSTRAINT "clintArchive_clint_id_fkey" FOREIGN KEY (clint_id) REFERENCES public.clints(id);


--
-- Name: clintArchive clintArchive_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."clintArchive"
    ADD CONSTRAINT "clintArchive_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: clint_documents clint_documents_clint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clint_documents
    ADD CONSTRAINT clint_documents_clint_id_fkey FOREIGN KEY (clint_id) REFERENCES public.clints(id) ON DELETE CASCADE;


--
-- Name: clint_documents clint_documents_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clint_documents
    ADD CONSTRAINT clint_documents_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: clints clints_clintArchive_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clints
    ADD CONSTRAINT "clints_clintArchive_id_fkey" FOREIGN KEY ("clintArchive_id") REFERENCES public."clintArchive"(id);


--
-- Name: clints clints_delinquencyReason_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clints
    ADD CONSTRAINT "clints_delinquencyReason_id_fkey" FOREIGN KEY ("delinquencyReason_id") REFERENCES public."delinquencyReason"(id);


--
-- Name: clints clints_portfolios_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clints
    ADD CONSTRAINT clints_portfolios_id_fkey FOREIGN KEY (portfolios_id) REFERENCES public.portfolios(id) ON DELETE CASCADE;


--
-- Name: clints clints_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clints
    ADD CONSTRAINT clints_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: notifications notifications_receiver_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_receiver_user_id_fkey FOREIGN KEY (receiver_user_id) REFERENCES public.users(id);


--
-- Name: notifications notifications_sender_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_sender_user_id_fkey FOREIGN KEY (sender_user_id) REFERENCES public.users(id);


--
-- Name: paymentDates paymentDates_clint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."paymentDates"
    ADD CONSTRAINT "paymentDates_clint_id_fkey" FOREIGN KEY (clint_id) REFERENCES public.clints(id) ON DELETE CASCADE;


--
-- Name: paymentDates paymentDates_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public."paymentDates"
    ADD CONSTRAINT "paymentDates_user_id_fkey" FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: portfolios portfolios_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolios
    ADD CONSTRAINT portfolios_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: supervisor_contracts supervisor_contracts_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supervisor_contracts
    ADD CONSTRAINT supervisor_contracts_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: supervisor_contracts supervisor_contracts_supervisor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supervisor_contracts
    ADD CONSTRAINT supervisor_contracts_supervisor_id_fkey FOREIGN KEY (supervisor_id) REFERENCES public.supervisors(id);


--
-- Name: supervisor_users supervisor_users_supervisor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supervisor_users
    ADD CONSTRAINT supervisor_users_supervisor_id_fkey FOREIGN KEY (supervisor_id) REFERENCES public.supervisors(id);


--
-- Name: supervisor_users supervisor_users_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supervisor_users
    ADD CONSTRAINT supervisor_users_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: supervisors supervisors_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.supervisors
    ADD CONSTRAINT supervisors_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: users users_branch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_branch_id_fkey FOREIGN KEY (branch_id) REFERENCES public.branches(id);


--
-- Name: users users_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id);


--
-- Name: users users_nationality_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_nationality_id_fkey FOREIGN KEY (nationality_id) REFERENCES public.nationalities(id);


--
-- Name: users users_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id);


--
-- PostgreSQL database dump complete
--


